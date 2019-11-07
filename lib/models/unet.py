import torch.nn as nn
import torch
from .basic_modules import ConvBnRelu, ConvBnLeakyRelu, RefineResidual


class UNet(nn.Module):
    def __init__(self, depth_channels=1, occ_channels=9):
        super(UNet, self).__init__()

        self.down_scale = nn.MaxPool2d(2)
        self.up_scale = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True)

        self.depth_down_layer0 = ConvBnLeakyRelu(depth_channels + occ_channels, 32, 3, 1, 1, 1, 1,\
                                     has_bn=True, leaky_alpha=0.3, \
                                     has_leaky_relu=True, inplace=True, has_bias=True)
        self.depth_down_layer1 = ConvBnLeakyRelu(32, 64, 3, 1, 1, 1, 1,\
                                     has_bn=True, leaky_alpha=0.3, \
                                     has_leaky_relu=True, inplace=True, has_bias=True)
        self.depth_down_layer2 = ConvBnLeakyRelu(64, 128, 3, 1, 1, 1, 1,\
                                     has_bn=True, leaky_alpha=0.3, \
                                     has_leaky_relu=True, inplace=True, has_bias=True)
        self.depth_down_layer3 = ConvBnLeakyRelu(128, 256, 3, 1, 1, 1, 1,\
                                     has_bn=True, leaky_alpha=0.3, \
                                     has_leaky_relu=True, inplace=True, has_bias=True)

        self.depth_down_layer4 = ConvBnLeakyRelu(256, 256, 3, 1, 1, 1, 1, \
                                                 has_bn=True, leaky_alpha=0.3, \
                                                 has_leaky_relu=True, inplace=True, has_bias=True)

        self.depth_up_layer0 = RefineResidual(256 * 2, 128, relu_layer='LeakyReLU', \
                                     has_bias=True, has_relu=True, leaky_alpha=0.3)
        self.depth_up_layer1 = RefineResidual(128 * 2, 64, relu_layer='LeakyReLU', \
                                    has_bias=True, has_relu=True, leaky_alpha=0.3)
        self.depth_up_layer2 = RefineResidual(64 * 2, 32, relu_layer='LeakyReLU', \
                                    has_bias=True, has_relu=True, leaky_alpha=0.3)
        self.depth_up_layer3 = RefineResidual(32 * 2, 32, relu_layer='LeakyReLU', \
                                    has_bias=True, has_relu=True, leaky_alpha=0.3)

        self.refine_layer0 = ConvBnLeakyRelu(32 + depth_channels, 16, 3, 1, 1, 1, 1,\
                                    has_bn=True, leaky_alpha=0.3, \
                                    has_leaky_relu=True, inplace=True, has_bias=True)
        self.refine_layer1 = ConvBnLeakyRelu(16, 10, 3, 1, 1, 1, 1,\
                                    has_bn=True, leaky_alpha=0.3, \
                                    has_leaky_relu=True, inplace=True, has_bias=True)

        self.output_layer = ConvBnRelu(10, 1, 3, 1, 1, 1, 1,\
                                     has_bn=False, \
                                     has_relu=False, inplace=True, has_bias=True)

    def forward(self, occ, x):
        m0 = torch.cat((occ, x), 1)
        #### Depth ####
        conv0 = self.depth_down_layer0(m0)
        x1 = self.down_scale(conv0)
        conv1 = self.depth_down_layer1(x1)
        x2 = self.down_scale(conv1)
        conv2 = self.depth_down_layer2(x2)
        x3 = self.down_scale(conv2)
        conv3 = self.depth_down_layer3(x3)
        x4 = self.down_scale(conv3)
        conv4 = self.depth_down_layer4(x4)

        #### Decode ####
        m4 = torch.cat((conv4, x4), 1)
        m4 = self.depth_up_layer0(m4)
        m3 = self.up_scale(m4)

        m3 = torch.cat((m3, x3), 1)
        m3 = self.depth_up_layer1(m3)
        m2 = self.up_scale(m3)

        m2 = torch.cat((m2, x2), 1)
        m2 = self.depth_up_layer2(m2)
        m1 = self.up_scale(m2)

        m1 = torch.cat((m1, x1), 1)
        m1 = self.depth_up_layer3(m1)
        m = self.up_scale(m1)

        ### Residual ###
        r = torch.cat((m, x), 1)
        r = self.refine_layer0(r)
        r = self.refine_layer1(r)
        r = self.output_layer(r)

        x = (x + r).relu()
        return x


if __name__ == "__main__":
    model = UNet()

    depth = torch.rand((4, 1, 480, 640))
    occ = torch.rand((4, 9, 480, 640))

    out = model(occ, depth)
    print(out.shape)
