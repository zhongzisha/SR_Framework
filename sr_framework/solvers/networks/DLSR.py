import torch
import torch.nn as nn
import torch.nn.functional as F
from torchsummaryX import summary


class ESA(nn.Module):
    def __init__(self, n_feats, conv):
        super(ESA, self).__init__()
        f = n_feats // 4
        self.conv1 = conv(n_feats, f, kernel_size=1)
        self.conv_f = conv(f, f, kernel_size=1)
        self.conv_max = conv(f, f, kernel_size=3, padding=1)
        self.conv2 = conv(f, f, kernel_size=3, stride=2, padding=0)
        self.conv3 = conv(f, f, kernel_size=3, padding=1)
        self.conv3_ = conv(f, f, kernel_size=3, padding=1)
        self.conv4 = conv(f, n_feats, kernel_size=1)
        self.sigmoid = nn.Sigmoid()
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        c1_ = (self.conv1(x))
        c1 = self.conv2(c1_)
        v_max = F.max_pool2d(c1, kernel_size=7, stride=3)
        v_range = self.relu(self.conv_max(v_max))
        c3 = self.relu(self.conv3(v_range))
        c3 = self.conv3_(c3)
        c3 = F.interpolate(c3, (x.size(2), x.size(3)), mode='bilinear', align_corners=False)
        cf = self.conv_f(c1_)
        c4 = self.conv4(c3 + cf)
        m = self.sigmoid(c4)

        return x * m


class SeparableConv(nn.Module):
    def __init__(self, n_feats=50, k=3):
        super(SeparableConv, self).__init__()
        self.separable_conv = nn.Sequential(
            nn.Conv2d(n_feats, 2 * n_feats, k, 1, (k - 1) // 2, groups=n_feats),
            nn.ReLU(True),
            nn.Conv2d(2 * n_feats, n_feats, 1, 1, 0),
        )
        self.act = nn.ReLU(True)

    def forward(self, x):
        out = self.separable_conv(x)
        out += x
        out = self.act(out)

        return out


class Cell(nn.Module):
    def __init__(self, n_feats=50):
        super(Cell, self).__init__()
        self.conv1x1 = nn.Conv2d(n_feats, n_feats, 1, 1, 0)
        self.separable_conv7x7 = SeparableConv(n_feats, k=7)
        self.separable_conv5x5 = SeparableConv(n_feats, k=5)
        self.fuse = nn.Conv2d(n_feats * 2, n_feats, 1, 1, 0)

        self.esa = ESA(n_feats, nn.Conv2d)

        self.branch = nn.ModuleList([nn.Conv2d(n_feats, n_feats // 2, 1, 1, 0) for _ in range(4)])

    def forward(self, x):
        out1 = self.conv1x1(x)
        out2 = self.separable_conv7x7(out1)
        out3 = self.separable_conv5x5(out2)

        # fuse [x, out1, out2, out3]
        out = self.fuse(
            torch.cat([self.branch[0](x), self.branch[1](out1), self.branch[2](out2), self.branch[3](out3)], dim=1))
        out = self.esa(out)
        out += x

        return out


class DLSR(nn.Module):
    def __init__(self, scale=4, in_channels=3, n_feats=56, out_channels=3):
        super(DLSR, self).__init__()
        self.head = nn.Conv2d(in_channels, n_feats, 3, 1, 1)
        # body cells
        self.cells = nn.ModuleList([Cell(n_feats) for _ in range(6)])

        # fusion
        self.local_fuse = nn.ModuleList([nn.Conv2d(n_feats * 2, n_feats, 1, 1, 0) for _ in range(4)])
        '''
        self.global_fuse = nn.Sequential(
            nn.Conv2d(n_feats*4, n_feats, 1, 1, 0),
            nn.ReLU(True),
        )
        '''
        self.tail = nn.Sequential(
            nn.Conv2d(n_feats, out_channels * (scale ** 2), 3, 1, 1),
            nn.PixelShuffle(scale)
        )

    def forward(self, x):
        # head
        out0 = self.head(x)

        # body cells
        out1 = self.cells[0](out0)
        out2 = self.cells[1](out1)
        out2_fuse = self.local_fuse[0](torch.cat([out1, out2], dim=1))
        out3 = self.cells[2](out2_fuse)
        out3_fuse = self.local_fuse[1](torch.cat([out2, out3], dim=1))
        out4 = self.cells[3](out3_fuse)
        out4_fuse = self.local_fuse[2](torch.cat([out2, out4], dim=1))
        out5 = self.cells[4](out4_fuse)
        out5_fuse = self.local_fuse[3](torch.cat([out4, out5], dim=1))
        out6 = self.cells[5](out5_fuse)

        # out = self.global_fuse(torch.cat([out3, out4, out5, out6], dim=1))
        out = out6
        out += out0

        # tail
        out = self.tail(out)

        return out


class Args:
    def __init__(self):
        self.scale = [4]


if __name__ == '__main__':
    args = Args()
    model = DLSR(scale=4, in_channels=3, n_feats=56, out_channels=3).to('cuda')
    in_ = torch.randn(1, 3, round(720 / args.scale[0]), round(1280 / args.scale[0])).to('cuda')
    summary(model, in_)
