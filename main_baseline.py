import os
import json
from torch import nn
from torch import optim

from opts import parse_opts
from lib.spatial_transforms import *

from data.train_data_loader import DataSet as train_DataSet
from data.test_data_loader import DataSet as test_DataSet
from cls import build_model
from models.teacher_student_net import teacher_student_net
import time
import datetime

from lib.utils import AverageMeter, calculate_accuracy
from torch.autograd import Variable
from torch.optim import lr_scheduler
from lib.multiloss import multiloss

import cv2
import pickle

import eval_res


# writer = SummaryWriter()


def get_mean(norm_value=255):
    return [114.7748 / norm_value, 107.7354 / norm_value, 99.4750 / norm_value]


def get_label(res_tensor):
    res_numpy=res_tensor.data.cpu().numpy()
    labels=[]
    for row in res_numpy:
        labels.append(np.argmax(row))
    return labels


def get_labels_from_candidate(video, temporal_length, model, spatial_transform, batch_size, device, boundary_index, **args):
    print(boundary_index)
    clip_batch = []
    labels = []
    all_frames = []
    info_boundary = []

    print("[INFO] transform video for test")
    video_length = 0
    for i, im in enumerate(video):
        frame = Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)).convert('RGB')
        frame = spatial_transform(frame)
        all_frames.append(frame)
        video_length+=1

    print("[INFO] start video test")
    for i, candidate_frame_number in enumerate(boundary_index):
        start_frame = int(candidate_frame_number-(temporal_length/2-1))
        start_frame = 0 if start_frame < 0 else start_frame
        end_frame = int(candidate_frame_number+(temporal_length/2)+1)
        end_frame = video_length if end_frame > video_length else end_frame

        image_clip = all_frames[start_frame:end_frame]
        info_boundary.append([start_frame, end_frame])
        image_clip += [image_clip[-1] for _ in range(temporal_length - len(image_clip))]

        if len(image_clip) == temporal_length:
            clip = torch.stack(image_clip, 0).permute(1, 0, 2, 3)
            clip_batch.append(clip)
            # image_clip = image_clip[int(temporal_length / 2):]

        if len(clip_batch) == batch_size or i==(len(boundary_index)-1):
            clip_tensor = torch.stack(clip_batch, 0)
            # Alexnet
            # clip_tensor = Variable(clip_tensor)
            # resnet
            clip_tensor = Variable(clip_tensor).cuda(device)
            results = model(clip_tensor)
            labels += get_label(results)
            clip_batch = []

    print("[INFO] get predicted label")
    res = [0]*video_length
    for i, label in enumerate(labels):
        range_of_frames = info_boundary[i]
        for j in range(range_of_frames[0], range_of_frames[1]):
            res[j] = label if label == 1 or res[j] == 0 else res[j]

    final_res = []
    i = 0
    while i < len(res):
        if res[i] > 0:
            label = res[i]
            begin = i
            i += 1
            while i < len(res) and res[i] == res[i - 1]:
                i += 1
            end = i - 1
            final_res.append((begin, end, label))
        else:
            i += 1
    return final_res


def get_result(labels, frame_pos, opt):
    # print(labels)
    # print(frame_pos, flush=True)
    cut_priority = False
    gradual_priority = False
    final_res = []
    for i, label in enumerate(labels):
        # cut, gradual only
        if label > 0:
            # transition 데이터가 없을 때
            if len(final_res) == 0:
                final_res.append((frame_pos[i], frame_pos[i] + opt.sample_duration - 1, label))
            else:
                last_boundary = final_res[-1][1]
                # 범위가 겹치지 않을때
                if last_boundary < frame_pos[i]:
                    final_res.append((frame_pos[i], frame_pos[i] + opt.sample_duration - 1, label))
                # 범위가 겹칠 때
                else:
                    start_boundary = final_res[-1][0]
                    last_label = final_res[-1][2]
                    # cut이 gradual보다 우선하는 정책
                    if cut_priority:
                        # 레이블이 같을 때
                        if last_label == label:
                            final_res[-1] = (start_boundary, frame_pos[i] + opt.sample_duration - 1, label)
                        # 나중에 나온 레이블이 cut
                        elif last_label < label:
                            final_res[-1] = (start_boundary, frame_pos[i] - 1, last_label)
                            final_res.append((frame_pos[i], frame_pos[i] + opt.sample_duration - 1, label))
                        # 나중에 나온 레이블이 gradual
                        else:
                            final_res.append((last_boundary + 1, frame_pos[i] + opt.sample_duration - 1, label))
                    # gradual이 cut보다 우선하는 정책
                    elif gradual_priority:
                        # 레이블이 같을 때
                        if last_label == label:
                            final_res[-1] = (start_boundary, frame_pos[i] + opt.sample_duration - 1, label)
                        # 나중에 나온 레이블이 gradual
                        elif last_label > label:
                            final_res[-1] = (start_boundary, frame_pos[i] - 1, last_label)
                            final_res.append((frame_pos[i], frame_pos[i] + opt.sample_duration - 1, label))
                        # 나중에 나온 레이블이 cut
                        else:
                            final_res.append((last_boundary + 1, frame_pos[i] + opt.sample_duration - 1, label))
                    # 나중에 오는 transition이 우선하는 정책
                    else:
                        if last_label == label:
                            final_res[-1] = (start_boundary, frame_pos[i] + opt.sample_duration - 1, label)
                        else:
                            final_res[-1] = (start_boundary, frame_pos[i] - 1, last_label)
                            final_res.append((frame_pos[i], frame_pos[i] + opt.sample_duration - 1, label))

        else:
            pass

    # i = 0
    # while i < len(labels):
    #     if labels[i] > 0:
    #         label = labels[i]
    #         begin = i
    #         i += 1
    #         while i < len(labels) and labels[i] == labels[i - 1]:
    #             i += 1
    #         end = i - 1
    #         final_res.append((begin * opt.sample_duration / 2 + 1, end * opt.sample_duration / 2 + 16 + 1, label))
    #     else:
    #         i += 1
    return final_res


def test(test_data_loader, model, device):
    labels = []
    frame_pos = []

    for _, (clip, boundary) in enumerate(test_data_loader):
        batch_time = time.time()
        print("batch {}".format(_ + 1), end=' ', flush=True)

        # clip = clip.to(device)
        clip = clip.cuda(device, non_blocking=True)
        clip = Variable(clip, requires_grad=False)
        results = model(clip)
        # if use teacher student network, only get result of student network output
        # if not opt.no_multiloss:
        #     results = results[1]

        labels += get_label(results)
        boundary = boundary.data.numpy()
        for _ in boundary:
            frame_pos.append(int(_+1))

        end_time = time.time() - batch_time
        print(" : {}".format(end_time), flush=True)

    return labels, frame_pos


def load_checkpoint(model, opt_model):
    if opt_model=='alexnet':
        path = 'models/Alexnet-final.pth'
    elif opt_model=='resnet' or opt_model=='resnext':
        path = 'results/model_final.pth'
    else:
        print("[ERR] incorrect opt.model : ", opt_model)
        assert False
    print("load model... : ", opt_model)
    checkpoint = torch.load(path)
    model.load_state_dict(checkpoint['state_dict'])


def save_pickle(labels_path, frame_pos_path, labels, frame_pos):
    with open(labels_path, 'wb') as f:
        pickle.dump(labels, f)
    with open(frame_pos_path, 'wb') as f:
        pickle.dump(frame_pos, f)


def load_pickle(labels_path, frame_pos_path):
    with open(labels_path, 'rb') as f:
        labels = pickle.load(f)
    with open(frame_pos_path, 'rb') as f:
        frame_pos = pickle.load(f)
    return labels, frame_pos


def test_misaeng(device):
    opt = parse_opts()

    if not opt.no_multiloss:
        teacher_model_path = 'models/Alexnet-final.pth'
        model = teacher_student_net(opt, teacher_model_path, 'test', device)
        print(model)
    else:
        model = build_model(opt, 'test', device)
    load_checkpoint(model, opt.model)
    model.eval()

    spatial_transform = get_test_spatial_transform(opt)
    temporal_transform = None
    target_transform = None

    root_dir = 'E:/video/misaeng'
    # print(root_dir, flush=True)
    # print(opt.test_list_path, flush=True)
    misaeng_list_path = 'E:/video/misaeng/misaeng_filename_list.txt'
    with open(misaeng_list_path, 'r') as f:
        video_name_list = [line.strip('\n') for line in f.readlines()]

    res = {}
    # print('\n====> Testing Start', flush=True)
    epoch_time = time.time()
    for idx, video_name in enumerate(video_name_list[:1]):
        video_time = time.time()

        print("Make dataset {}...".format(video_name), flush=True)
        test_data = test_DataSet(root_dir, video_name,
                                 spatial_transform=spatial_transform,
                                 temporal_transform=temporal_transform,
                                 target_transform=target_transform,
                                 sample_duration=opt.sample_duration,
                                 no_candidate=opt.no_candidate)
        test_data_loader = torch.utils.data.DataLoader(test_data, batch_size=opt.batch_size,
                                                       num_workers=opt.n_threads, pin_memory=True)

        total_frame, fps = test_data.get_video_attr()

        # 이미 처리한 결과가 있다면 pickle 로드
        print("Process {}".format(idx + 1), flush=True)
        is_full_data = '.full' if True else '.no_full'
        dir = 'teacher' if not opt.no_multiloss else opt.model
        labels_path = os.path.join(root_dir, dir, video_name + is_full_data + '.labels')
        frame_pos_path = os.path.join(root_dir, dir, video_name + is_full_data + '.frame_pos')
        if not os.path.exists(labels_path) and not os.path.exists(frame_pos_path):
            labels, frame_pos = test(test_data_loader, model, device)
            save_pickle(labels_path, frame_pos_path, labels, frame_pos)
        else:
            labels, frame_pos = load_pickle(labels_path, frame_pos_path)

        final_res = get_result(labels, frame_pos, opt)
        # print(final_res)

        boundary_index_final = []
        _res = {'cut': [], 'gradual': []}
        for begin, end, label in final_res:
            if label == 2:
                _res['cut'].append((begin, end))
            else:
                _res['gradual'].append((begin, end))
            boundary_index_final.append([begin, end, label])
        res[video_name] = _res

        boundary_index_final.insert(0, 1)
        boundary_index_final.append(total_frame)

        srt_index = 0
        do_srt = True
        if do_srt:
            with open(os.path.join(root_dir, video_name) + '.final.srt', 'w', encoding='utf-8') as f:
                for bound_ind in range(len(boundary_index_final) - 1):
                    if type(boundary_index_final[bound_ind]) == list:
                        transition = 'cut' if boundary_index_final[bound_ind][2] == 2 else 'gradual'
                        startframe = boundary_index_final[bound_ind][0]
                        endframe = boundary_index_final[bound_ind][1]
                        starttime = float(startframe / fps)
                        endtime = float(endframe / fps)
                        f.write(str(srt_index) + '\n')
                        f.write(str(starttime) + ' --> ' + str(endtime) + '\n')
                        f.write(transition + ': frame [' + str(startframe) + ',' + str(endframe) + ']\n')
                        f.write('\n')

                        if endframe == total_frame:
                            break

                    startframe = boundary_index_final[bound_ind][1] + 1 if bound_ind != 0 else 1
                    endframe = boundary_index_final[bound_ind + 1][0] - 1 if bound_ind != (
                                len(boundary_index_final) - 2) else boundary_index_final[bound_ind + 1]
                    starttime = float(startframe / fps)
                    endtime = float(endframe / fps)
                    f.write(str(srt_index) + '\n')
                    f.write(str(starttime) + ' --> ' + str(endtime) + '\n')
                    f.write('shot# ' + str(bound_ind) + ' & frame [' + str(startframe) + ',' + str(endframe) + ']\n')
                    f.write('\n')

                    srt_index += 1

        end_time = time.time() - video_time
        end_time = datetime.timedelta(seconds=end_time)
        print("{} Processing time : {}".format(video_name, end_time))

    total_time = time.time() - epoch_time
    total_time = datetime.timedelta(seconds=total_time)
    print("[INFO] finish test!!, {}".format(total_time), flush=True)

    out_path = os.path.join(root_dir, 'miseang_results.json')
    if not os.path.exists(out_path):
        json.dump(res, open(out_path, 'w'))


def test_dataset(device):
    opt = parse_opts()

    if not opt.no_multiloss:
        teacher_model_path = 'models/Alexnet-final.pth'
        model = teacher_student_net(opt, teacher_model_path, 'test', device)
        print(model)
    else:
        model = build_model(opt, 'test', device)
    load_checkpoint(model, opt.model)
    model.eval()

    spatial_transform = get_test_spatial_transform(opt)
    temporal_transform = None
    target_transform = None
    # list_root_path : train path, only_gradual path
    # `19.3.7 : add only_gradual path
    root_dir = os.path.join(opt.root_dir, opt.test_subdir)
    # print(root_dir, flush=True)
    # print(opt.test_list_path, flush=True)
    with open(opt.test_list_path, 'r') as f:
        video_name_list = [line.strip('\n') for line in f.readlines()]

    res = {}
    # print('\n====> Testing Start', flush=True)
    epoch_time = time.time()
    for idx, video_name in enumerate(video_name_list):
        video_time = time.time()
        print("Process {}".format(idx+1), end=' ', flush=True)
        test_data = test_DataSet(root_dir, video_name,
                                 spatial_transform=spatial_transform,
                                 temporal_transform=temporal_transform,
                                 target_transform=target_transform,
                                 sample_duration=opt.sample_duration,
                                 no_candidate=opt.no_candidate)
        test_data_loader = torch.utils.data.DataLoader(test_data, batch_size=opt.batch_size,
                                                       num_workers=opt.n_threads, pin_memory=True)
        is_full_data = '.full' if opt.is_full_data else '.no_full'
        dir = 'KD' if not opt.no_multiloss else opt.model
        labels_path = os.path.join(root_dir, dir, video_name + is_full_data + '.labels')
        frame_pos_path = os.path.join(root_dir, dir, video_name + is_full_data + '.frame_pos')
        if not os.path.exists(labels_path) and not os.path.exists(frame_pos_path):
            labels, frame_pos = test(test_data_loader, model, device)
            save_pickle(labels_path, frame_pos_path, labels, frame_pos)
        else:
            labels, frame_pos = load_pickle(labels_path, frame_pos_path)

        final_res = get_result(labels, frame_pos, opt)
        # print(final_res)

        _res = {'cut': [], 'gradual': []}
        for begin, end, label in final_res:
            if label == 2:
                _res['cut'].append((begin, end))
            else:
                _res['gradual'].append((begin, end))
        res[video_name] = _res
        # print(videoname," : ", _res)

        # json.dump(res, open("test.json", 'w'))

        end_time = time.time() - video_time
        print("{} Processing time : {:.3f}s".format(video_name, end_time))

    total_time = time.time() - epoch_time
    total_time = datetime.timedelta(seconds=total_time)
    print("[INFO] finish test!!, {}".format(total_time), flush=True)

    out_path = os.path.join(opt.result_dir, 'results.json')
    if not os.path.exists(out_path):
        json.dump(res, open(out_path, 'w'))
    eval_res.eval(out_path, opt.gt_dir)


def calculate_accuracy(outputs, targets):
    batch_size = targets.size(0)

    _, pred = outputs.topk(1, 1, True)
    pred = pred.t()
    correct = pred.eq(targets.view(1, -1))
    n_correct_elems = correct.float().sum().data

    return n_correct_elems / batch_size


# 19.3.8 revision
# add parameter : "device"
def train(cur_iter, iter_per_epoch, epoch, data_loader, model, criterion, optimizer, scheduler, opt, device):
    # 19.3.14. add
    # print("device : ", torch.cuda.get_device_name(0), flush=True)
    # torch.set_default_tensor_type('torch.cuda.DoubleTensor')
    # 19.5.10. revision
    for i in range(opt.gpu_num):
        print("device {} : {}".format(i, torch.cuda.get_device_name(i)), flush=True)

    # batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    accuracies = AverageMeter()

    i = cur_iter
    total_acc = [0.0] * epoch
    epoch_acc = 0.0

    # for debug
    # print(not(opt.no_cuda)) : True

    total_iter = epoch * iter_per_epoch
    save_timing = int(iter_per_epoch / 5)
    if save_timing < 2000:
        save_timing = 2000
    elif save_timing > 5000:
        save_timing = 5000
    epoch_time = time.time()
    print('\n====> Training Start', flush=True)
    while i < total_iter:
        for _, (inputs, targets) in enumerate(data_loader):
            start_time = time.time()

            # 19.3.7 add
            # if not opt.no_cuda:
            #     targets = targets.cuda(async=True)
            #     inputs = inputs.cuda(async=True)

            # 19.3.8. revision
            if not opt.no_cuda:
                targets = targets.cuda(device, non_blocking=True)
                inputs = inputs.cuda(device, non_blocking=True)

            targets = Variable(targets)
            inputs = Variable(inputs)

            outputs = model(inputs)

            loss = criterion(outputs, targets)

            if not opt.no_multiloss:
                outputs = outputs[1]

            acc = calculate_accuracy(outputs, targets)
            epoch_acc += acc / iter_per_epoch

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            scheduler.step(loss.data)

            batch_time = time.time() - start_time

            print('Iter:{} Loss_conf:{} acc:{} lr:{} batch_time:{:.3f}s'.format(
                i + 1, loss.data, acc, optimizer.param_groups[0]['lr'], batch_time), flush=True)
            i += 1

            if i % save_timing == 0:
                save_file_path = os.path.join(opt.result_dir, 'model_iter{}.pth'.format(i))
                print("save to {}".format(save_file_path))
                states = {
                    'state_dict': model.state_dict(),
                    'optimizer': optimizer.state_dict(),
                }
                torch.save(states, save_file_path)
            if i % iter_per_epoch == 0 and i != 0:
                print("epoch {} accuracy : {}".format(i / iter_per_epoch, epoch_acc))
                total_acc[int(i / iter_per_epoch)-1] = epoch_acc
                epoch_acc = 0.0
            if i >= total_iter:
                break

    total_time = time.time() - epoch_time
    total_time = datetime.timedelta(seconds=total_time)
    print("Training Time : {}".format(total_time), flush=True)

    save_file_path = os.path.join(opt.result_dir, 'model_final.pth'.format(opt.checkpoint_path))
    print("save to {}".format(save_file_path), flush=True)
    states = {
        'state_dict': model.state_dict(),
        'optimizer': optimizer.state_dict(),
    }
    torch.save(states, save_file_path)

    json.dump(total_acc, open(os.path.join(opt.result_dir,'epoch_accuracy.json'),'w'))


def get_lastest_model(opt):
    if opt.resume_path != '':
        return 0
    if os.path.exists(os.path.join(opt.result_dir, 'model_final.pth')):
        opt.resume_path = os.path.join(opt.result_dir, 'model_final.pth')
        return opt.epoch * opt.iter_per_epoch

    iter_num = -1
    for filename in os.listdir(opt.result_dir):
        if filename[-3:] == 'pth':
            _iter_num = int(filename[len('model_iter'):-4])
            iter_num = max(iter_num, _iter_num)
    if iter_num > 0:
        opt.resume_path = os.path.join(opt.result_dir, 'model_iter{}.pth'.format(iter_num))
    return iter_num


def train_misaeng(device):
    opt = parse_opts()

    opt.scales = [opt.initial_scale]
    for i in range(1, opt.n_scales):
        opt.scales.append(opt.scales[-1] * opt.scale_step)

    opt.mean = get_mean(opt.norm_value)
    print(opt)

    torch.manual_seed(opt.manual_seed)

    # 19.3.8. add
    print("cuda is available : ", torch.cuda.is_available(), flush=True)
    print('[INFO] training {}'.format(opt.model), flush=True)

    # 19.5.7. add
    # teacher student option add
    if not opt.no_multiloss:
        teacher_model_path = 'models/Alexnet-final.pth'
        model = teacher_student_net(opt, teacher_model_path, 'train', device)
        print(model)
    else:
        model = build_model(opt, 'train', device)
        model.train()

    cur_iter = 0
    if opt.auto_resume and opt.resume_path == '':
        cur_iter = get_lastest_model(opt)
    if opt.resume_path:
        print('loading checkpoint {}'.format(opt.resume_path), flush=True)
        checkpoint = torch.load(opt.resume_path)
        model.load_state_dict(checkpoint['state_dict'])

    parameters = model.parameters()

    # 19.5.7. add
    # teacher student option add
    if not opt.no_multiloss:
        criterion = multiloss()
    else:
        criterion = nn.CrossEntropyLoss()

    if opt.nesterov:
        dampening = 0
    else:
        dampening = opt.momentum

    optimizer = optim.SGD(parameters, lr=opt.learning_rate,
                          momentum=opt.momentum, dampening=dampening,
                          weight_decay=opt.weight_decay, nesterov=opt.nesterov)

    # 19.3.8 revision
    if not opt.no_cuda:
        criterion = criterion.to(device)

    if not opt.no_train:
        spatial_transform = get_train_spatial_transform(opt)
        temporal_transform = None
        target_transform = None
        # list_root_path : train path, only_gradual path
        # `19.3.7 : add only_gradual path
        list_root_path = list()
        list_root_path.append(os.path.join(opt.root_dir, opt.train_subdir))
        list_root_path.append(os.path.join(opt.root_dir, 'only_gradual'))
        print(list_root_path, flush=True)
        print("[INFO] reading : ", opt.video_list_path, flush=True)
        training_data = train_DataSet(list_root_path, opt.video_list_path, opt,
                                      spatial_transform=spatial_transform,
                                      temporal_transform=temporal_transform,
                                      target_transform=target_transform, sample_duration=opt.sample_duration)

        weights = torch.DoubleTensor(training_data.weights)
        sampler = torch.utils.data.sampler.WeightedRandomSampler(weights, len(weights))

        training_data_loader = torch.utils.data.DataLoader(training_data, batch_size=opt.batch_size,
                                                           num_workers=opt.n_threads, sampler=sampler, pin_memory=True)

        scheduler = lr_scheduler.ReduceLROnPlateau(optimizer, 'min', patience=opt.iter_per_epoch)

        # 19.3.8. add
        # train(cur_iter,opt.total_iter,training_data_loader, model, criterion, optimizer,scheduler,opt)
        train(cur_iter, opt.iter_per_epoch, opt.epoch, training_data_loader, model, criterion, optimizer, scheduler, opt, device)


if __name__ == '__main__':
    # 19.5.7 add
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    train_misaeng(device)
    # test_dataset(device)
    # test_misaeng(device)