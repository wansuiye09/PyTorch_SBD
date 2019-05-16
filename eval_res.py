import json
import os
from opts import parse_opts


def if_overlap(begin1, end1, begin2, end2):
    if begin1 > begin2:
        begin1, end1, begin2, end2 = begin2, end2, begin1, end1

    return end1 >= begin2


def get_union_cnt(set1, set2):
    cnt = 0
    for begin, end in set1:
        for _begin, _end in set2:
            if if_overlap(begin, end, _begin, _end):
                cnt += 1
                break
    return cnt


def recall_pre_f1(a, b, c):
    recall = a / b if b != 0 else 0
    precison = a / c if c != 0 else 0
    f1 = 2 * recall * precison / (recall + precison)
    return precison, recall, f1


def eval(predict_path, gt_path):
    predicts = json.load(open(predict_path))
    gts = json.load(open(gt_path))
    print(len(predicts))

    cut_correct_sum = 0
    gradual_correct_sum = 0
    all_correct_sum = 0
    gt_cut_sum = 0
    gt_gradual_sum = 0
    predict_cut_sum = 0
    predict_gradual_sum = 0
    for videoname, labels in gts.items():
        if videoname in predicts:
            _gts = gts[videoname]['transitions']
            gt_cuts = [(begin, end) for begin, end in _gts if end - begin == 1]
            gt_graduals = [(begin, end) for begin, end in _gts if end - begin > 1]

            _predicts = predicts[videoname]
            predicts_cut = _predicts['cut']
            predicts_gradual = _predicts['gradual']

            cut_correct = get_union_cnt(gt_cuts, predicts_cut)
            gradual_correct = get_union_cnt(gt_graduals, predicts_gradual)
            all_correct = get_union_cnt(predicts_cut + predicts_gradual, _gts)

            cut_correct_sum += cut_correct
            gradual_correct_sum += gradual_correct
            all_correct_sum += all_correct

            gt_cut_sum += len(gt_cuts)
            gt_gradual_sum += len(gt_graduals)

            predict_cut_sum += len(predicts_cut)
            predict_gradual_sum += len(predicts_gradual)
        else:
            print("{} not found".format(videoname))
            raise Exception()

    print("group\tprecision\trecall\tf1score")
    print("cut\t{}\t{}\t{}".format(*recall_pre_f1(cut_correct_sum, gt_cut_sum, predict_cut_sum)))
    print("gradual\t{}\t{}\t{}".format(*recall_pre_f1(gradual_correct_sum, gt_gradual_sum, predict_gradual_sum)))
    print("all\t{}\t{}\t{}".format(
        *recall_pre_f1(all_correct_sum, gt_cut_sum + gt_gradual_sum, predict_cut_sum + predict_gradual_sum)))


def candidate_eval(opt, gt_path):
    import torch
    from models.squeezenet import SqueezeNetFeature
    from lib.candidate_extracting import candidate_extraction

    device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    model = SqueezeNetFeature().cuda(device)
    root_dir = os.path.join(opt.root_dir, opt.test_subdir)
    # print(root_dir, flush=True)
    # print(opt.test_list_path, flush=True)
    with open(opt.test_list_path, 'r') as f:
        video_name_list = [line.strip('\n') for line in f.readlines()]


    res = {}
    # print('\n====> Testing Start', flush=True)
    gts = json.load(open(gt_path))
    result = {}
    for idx, videoname in enumerate(video_name_list):
        print("Process {} {}".format(idx + 1, videoname), flush=True)
        result[videoname] = {}
        boundary_index_list = candidate_extraction(root_dir, videoname, model, adjacent=True)
        labels = gts[videoname]["transitions"]
        total_length = len(labels)
        count_included = 0

        boundary_index = 0
        boundary = boundary_index_list[0]
        for label in labels:
            while boundary < len(boundary_index_list):
                boundary = boundary_index_list[boundary_index]
                if boundary < label[0]:
                    pass
                elif label[0] <= boundary and boundary <= label[1]:
                    count_included += 1
                else:
                    break
                boundary_index += 1
        if total_length != 0:
            result[videoname] = count_included / total_length
        else:
            result[videoname] = 1.0

    return result


if __name__ == "__main__":
    opt = parse_opts()
    check_gt = False
    check_candidate = False
    if check_gt:
        out_path = os.path.join(opt.result_dir, 'results.json')
        eval(out_path,opt.gt_dir)
    elif check_candidate:
        if not os.path.exists('candidate_result.json'):
            result = candidate_eval(opt, opt.gt_dir)
            json.dump(result, open('candidate_result.json', 'w'))
        else:
            result = json.load(open('candidate_result.json'))
            print(result)
            total = 0
            for _, value in result.items():
                total += value
            print(total / len(result.keys()))