import os.path

from detectron2.data import DatasetCatalog, MetadataCatalog
from detectron2.evaluation import COCOEvaluator, inference_on_dataset, DatasetEvaluator
import matplotlib.pyplot as plt
import torch

import numpy as np
from PIL import Image, ImageDraw
import time



def poly_to_mask(polygon, width, height):
    img = Image.new('L', (width, height), 0)
    for poly in polygon:
        ImageDraw.Draw(img).polygon(poly, outline=1, fill=1)
    mask = np.array(img)
    return mask


def decode_segmentation_masks(mask, colormap, n_classes):
    r = np.zeros_like(mask).astype(np.uint8)
    g = np.zeros_like(mask).astype(np.uint8)
    b = np.zeros_like(mask).astype(np.uint8)
    for l in range(0, n_classes):
        idx = mask == l
        r[idx] = colormap[l, 0]
        g[idx] = colormap[l, 1]
        b[idx] = colormap[l, 2]
    rgb = np.stack([r, g, b], axis=2)
    return rgb


def plot_mask(mask, colormap, classes=20, row=1, mask_name=None):
    col = ((mask.size(0)) // row) + 2
    fig, ax = plt.subplots(col, row, figsize=(10, 10))
    for i in range(mask.size(0)):
        mask[mask >= 7 ] = 0
        prediction_colormap = decode_segmentation_masks(mask[i].squeeze().cpu().numpy(), colormap, 7)
        #save the mask
        if mask_name is not None:
            Image.fromarray(prediction_colormap).save(mask_name+'_'+str(i)+'.png')
        ax[i // row, i % row].imshow(prediction_colormap)
    if mask_name is not None:
        plt.savefig(mask_name)


def voc_ap(rec, prec, use_07_metric=False):
    """ ap = voc_ap(rec, prec, [use_07_metric])
    Compute VOC AP given precision and recall.
    If use_07_metric is true, uses the
    VOC 07 11 point method (default:False).
    """
    if use_07_metric:
        # 11 point metric
        ap = 0.
        for t in np.arange(0., 1.1, 0.1):
            if np.sum(rec >= t) == 0:
                p = 0
            else:
                p = np.max(prec[rec >= t])
            ap = ap + p / 11.
    else:
        # correct AP calculation
        # first append sentinel values at the end
        mrec = np.concatenate(([0.], rec, [1.]))
        mpre = np.concatenate(([0.], prec, [0.]))

        # compute the precision envelope
        for i in range(mpre.size - 1, 0, -1):
            mpre[i - 1] = np.maximum(mpre[i - 1], mpre[i])

        # to calculate area under PR curve, look for points
        # where X axis (recall) changes value
        i = np.where(mrec[1:] != mrec[:-1])[0]

        # and sum (\Delta recall) * prec
        ap = np.sum((mrec[i + 1] - mrec[i]) * mpre[i + 1])
    return ap


def fast_hist(a, b, n):
    k = (a >= 0) & (a < n)
    return np.bincount(n * a[k].astype(int) + b[k], minlength=n ** 2).reshape(n, n)


def cal_one_mean_iou(image_array, label_array, num_parsing):
    hist = fast_hist(label_array, image_array, num_parsing).astype(np.float)
    num_cor_pix = np.diag(hist)
    num_gt_pix = hist.sum(1)
    union = num_gt_pix + hist.sum(0) - num_cor_pix
    iu = num_cor_pix / union
    return iu


class ParsingEval(DatasetEvaluator):

    def __init__(self, dataset_name, cfg, distributed, output_dir=None):
        super().__init__()
        self._cfg = cfg.clone()
        self._dataset_name = dataset_name
        self._distributed = distributed
        self._output_dir = output_dir
        self.dataset_dicts = DatasetCatalog.get(dataset_name)
        self.metadata = MetadataCatalog.get(dataset_name)
        self.ovthresh_seg = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        # if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
        except OSError:
            pass
    def reset(self):
        self.tp = {}
        self.fp = {}
        for i in self.ovthresh_seg:
            self.tp[i] = []
            self.fp[i] = []
        self.npos = 0
        self.total_time = 0
        self.delta_time = time.time()
        self.num_images = 0

    def process(self, inputs, outputs):
        self.num_images += len(inputs)
        self.total_time += (time.time() - self.delta_time)
        for input, output in zip(inputs, outputs):
            # save input image



            # self.npos += len(self.dataset_dicts[input['image_id']]['annotations'])
            if len(output["instances"]) == 0:
                seg_gt = self.mix_parts_of_instance(self.dataset_dicts[input['image_id']]['annotations'], (100, 100))
                self.npos += seg_gt.shape[0]
                continue
            w, h = output["instances"].pred_masks.size(1), output["instances"].pred_masks.size(2)
            seg_gt = self.mix_parts_of_instance(self.dataset_dicts[input['image_id']]['annotations'], (w, h))
            self.npos += seg_gt.size(0)
            # print(seg_gt.shape)
            seg_pred = output["instances"].pred_masks
            # seg_pred = seg_gt.clone()
            list_mious = []

            for i in range(seg_pred.size(0)):
                max_iou = 0
                max_iou_id = -1
                a = seg_pred[i].clone().to('cpu')
                for j in range(seg_gt.size(0)):
                    b = seg_gt[j].clone().to('cpu')
                    b[b >= 7] = 0
                    # a[a == 15] = 14
                    # b[b == 15] = 14
                    # a[a == 17] = 16
                    # b[b == 17] = 16
                    # a[a == 19] = 18
                    # b[b == 19] = 18
                    # a[a == 6] = 5
                    # b[b == 6] = 5
                    # a[a == 7] = 5
                    # b[b == 7] = 5
                    # a[a == 10] = 5
                    # b[b == 10] = 5
                    # a[a == 12] = 5
                    # b[b == 12] = 5
                    # a[a > 0] = 1
                    # b[b > 0] = 1

                    # print(a.unique())
                    # print(b.unique())
                    seg_iou = cal_one_mean_iou(a.numpy().astype(np.uint8), b.numpy().astype(np.uint8), 7)
                    # print(seg_iou)
                    # seg_iou = seg_iou[b.unique().cpu().numpy().astype(np.uint8)]
                    # seg_iou[seg_iou == 0] = np.nan
                    mean_seg_iou = np.nanmean(seg_iou[1:])
                    # print(mean_seg_iou)
                    if mean_seg_iou > max_iou:
                        max_iou = mean_seg_iou
                        max_iou_id = j

                list_mious.append({"id": max_iou_id, "iou": max_iou})

            list_mious = sorted(list_mious, key=lambda x: x["iou"], reverse=True)
            print([f"{x['id']}:{x['iou']:.3f}" for x in list_mious])
            for j in self.ovthresh_seg:
                id_list = []
                for i in list_mious:
                    if i['id'] not in id_list:
                        if i["iou"] >= j:
                            id_list.append(i['id'])
                            self.tp[j].append(1)
                            self.fp[j].append(0)
                        else:
                            self.tp[j].append(0)
                            self.fp[j].append(1)
                    else:
                        # pass
                        self.tp[j].append(0)
                        self.fp[j].append(1)

            # plot_mask(seg_gt, self.dataset_dicts.colormap, 20, 2, os.path.join(self._output_dir, str(input['image_id']) + "_gt.png"))
            # plot_mask(seg_pred, self.dataset_dicts.colormap, 20, 2, os.path.join(self._output_dir, str(input['image_id']) + "_pred.png"))
            # img = input["image"].permute(1, 2, 0).cpu().numpy()
            # img = (img * 255).astype(np.uint8)
            # Image.fromarray(img).save(os.path.join(self._output_dir, str(input['image_id']) + ".png"))
            # plt.show()
        # self.evaluate()
        self.delta_time = time.time()
        # return self.evaluate()

    def mix_parts_of_instance(self, instances, size):
        person_ids = set()
        for i in instances:
            person_ids.add(i['parent_id'])

        h, w = size
        seg_mask = torch.zeros((len(person_ids), h, w))
        # print(person_ids)
        for i in person_ids:
            for j in instances:
                if j['parent_id'] == i:
                    mask = poly_to_mask(j['segmentation'], w, h)
                    mask = torch.from_numpy(mask)
                    seg_mask[i] = torch.add(seg_mask[i], mask * (j['category_id'] + 0))

        return seg_mask

    def evaluate(self):
        result = {}
        for i in self.ovthresh_seg:
            tp = np.array(self.tp[i])
            fp = np.array(self.fp[i])
            tp = np.cumsum(tp)
            fp = np.cumsum(fp)
            rec = tp / self.npos
            prec = tp / np.maximum(tp + fp, np.finfo(np.float64).eps)

            ap = voc_ap(rec, prec)
            print(f"APp@{i}: {ap:.3f}, {self.npos}, {tp[-1]}, {fp[-1]}")
            result[f"APp@{i}"] = ap

        result["APpvol"] = sum(result.values()) / len(result)
        result["total_time"] = self.total_time
        result["fps"] = self.num_images / self.total_time
        print(f"APpvol: {result['APpvol']:.3f}")
        print(f"total_time: {result['total_time']:.2f}")
        print(f"fps: {result['fps']:.2f}")
        return result
