from models.deepSBD import *
from opts import parse_opts
from cls import build_model
import os


class teacher_student_net(nn.Module):
    def __init__(self, opt, teacher_model_path, phase):
        self.phase = phase
        # self.device = device
        super(teacher_student_net, self).__init__()

        opt.model = 'alexnet'
        self.teacher_model = build_model(opt, 'test')
        self.teacher_model_path = teacher_model_path
        self.load_checkpoint(self.teacher_model, self.teacher_model_path)

        opt.model = 'resnext'
        self.student_model = build_model(opt, 'train')

    def forward(self, x):
        # x = x.to(self.device)
        # x = x.cuda()
        if self.phase == 'train':
            teacher_x = self.teacher_model(x)
            student_x = self.student_model(x)
            out = (teacher_x, student_x)
        else:
            student_x = self.student_model(x)
            out = student_x

        return out

    def load_checkpoint(self, model, path):
        checkpoint = torch.load(path)
        model.load_state_dict(checkpoint['state_dict'])


if __name__ == '__main__':
    opt = parse_opts()
    opt.pretrain_path = '../kinetics_pretrained_model/resnext-101-kinetics.pth'
    teacher_model_path = 'Alexnet-final.pth'
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = teacher_student_net(opt, teacher_model_path, 'train')
    print(model)
