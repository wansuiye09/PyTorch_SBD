import torch
import torch.nn as nn


class MultiDetector(nn.Module):
    def __init__(self, in_planes, kernel_size=(3, 3, 3), num_classes=3):
        super(MultiDetector, self).__init__()

        self.loc_avgpool = nn.AvgPool3d(kernel_size, stride=1)
        # self.loc_layer = nn.Conv3d(in_planes, 2,
        #                            kernel_size=kernel_size, padding=0)
        self.loc_layer = nn.Linear(in_planes, 2)
        self.conf_avgpool = nn.AvgPool3d(kernel_size, stride=1)
        self.conf_layer = nn.Linear(in_planes, num_classes)

    def forward(self, x):
        loc_pool = self.loc_avgpool(x)
        loc_pool = loc_pool.view(loc_pool.size(0), -1)
        loc_x = self.loc_layer(loc_pool)

        conf_pool = self.conf_avgpool(x)
        conf_pool = conf_pool.view(conf_pool.size(0), -1)
        conf_x = self.conf_layer(conf_pool)

        # if loc_x.size(0) == 1:
        #     loc_x = loc_x.squeeze()
        #     loc_x = loc_x.unsqueeze(0)
        # else:
        #     loc_x = loc_x.squeeze()

        out = (loc_x, conf_x)
        return out


if __name__ == '__main__':
    kernel_size = (16, 4, 4)
    layer = MultiDetector(512 * 4, kernel_size=kernel_size)
    print(layer)
    input_t = torch.ones([1, 512*4, 16, 4, 4], dtype=torch.float32)
    loc, conf = layer(input_t)
    print('loc : {}, size : {}'.format(loc, loc.size()))
    print('conf : {}, size : {}'.format(conf, conf.size()))
