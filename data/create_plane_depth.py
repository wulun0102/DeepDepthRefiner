import argparse
import os
import numpy as np
import pandas as pd
import cv2
from math import atan, tan, pi
from tqdm import tqdm
import itertools


def point_to_plane(depth, fx=600, fy=600):
    H, W = depth.shape
    depth_plane = depth.copy()

    # compute field of view
    fov_x = 2 * atan(W / (2 * fx))
    fov_y = 2 * atan(H / (2 * fy))

    for i, j in itertools.product(range(H), range(W)):
        alpha_x = (pi - fov_x) / 2
        gamma_x = alpha_x + fov_x * ((W - j) / W)

        alpha_y = (pi - fov_y) / 2
        gamma_y = alpha_y + fov_y * ((H - i) / H)

        depth_plane[i, j] = np.sqrt(depth[i, j] ** 2 / (1 + 1 / (tan(gamma_x) ** 2) + 1 / (tan(gamma_y) ** 2)))

    return depth_plane.astype(depth.dtype)


parser = argparse.ArgumentParser(description='Transform depth maps to point cloud given the camera intrinsics')

parser.add_argument('--data_dir', type=str, default=None, help='path to interior net dataset')
parser.add_argument('--gt_dir', type=str, default='data', help='folder containing depth map')
parser.add_argument('--csv_file', type=str, default='InteriorNet.txt', help='csv file')
parser.add_argument('--label_name', type=str, default='_raycastingV2', help='occlusion label name')
parser.add_argument('--depth_ext', type=str, default='-depth.png')
parser.add_argument('--depth_plane_ext', type=str, default='-depth-plane.png')

opt = parser.parse_args()

df = pd.read_csv(os.path.join(opt.data_dir, opt.csv_file))

for index in tqdm(range(len(df))):
    depth_path = os.path.join(opt.data_dir, opt.gt_dir,
                              '{}{}'.format(df.iloc[index]['scene'], opt.label_name),
                              '{:04d}{}'.format(df.iloc[index]['image'], opt.depth_ext))

    depth_plane_path = depth_path.replace(opt.depth_ext, opt.depth_plane_ext)
    if os.path.exists(depth_plane_path):
        continue

    depth = cv2.imread(depth_path, -1)
    depth_plane = point_to_plane(depth)
    cv2.imwrite(depth_plane_path, depth_plane)

    assert os.path.exists(depth_plane_path)

