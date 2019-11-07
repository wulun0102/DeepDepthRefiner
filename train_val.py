import argparse
import numpy as np
import torch
from torch.utils.data import DataLoader
import torch.optim as optim
import os
import time

from lib.models.unet import UNet
from lib.models.ynet import YNet
from lib.datasets.ibims import Ibims
from lib.datasets.interior_net import InteriorNet

from lib.utils.net_utils import kaiming_init, save_checkpoint, load_checkpoint, \
    berhu_loss, spatial_gradient_loss, occlusion_aware_loss, create_gamma_matrix
from lib.utils.evaluate_ibims_error_metrics import compute_global_errors, \
    compute_depth_boundary_error, compute_directed_depth_error

# =================PARAMETERS=============================== #
parser = argparse.ArgumentParser()

# network and loss settings
parser.add_argument('--model', type=str, default='unet', help='resume checkpoint or not')
parser.add_argument('--linear', action='store_true', help='linear geometric occlusion-depth loss')
parser.add_argument('--alpha_depth', type=float, default=1., help='weight balance')
parser.add_argument('--alpha_occ', type=float, default=1., help='weight balance')

# optimization settings
parser.add_argument('--lr', type=float, default=0.0001, help='learning rate of optimizer')
parser.add_argument('--step', type=int, default=50, help='epoch to decrease')
parser.add_argument('--batch_size', type=int, default=8, help='input batch size')
parser.add_argument('--workers', type=int, help='number of data loading workers', default=4)
parser.add_argument('--epoch', type=int, default=100, help='number of epochs to train for')
parser.add_argument('--print_freq', type=int, default=50, help='frequence of output print')

# pth settings
parser.add_argument('--session', type=int, default=0, help='training session')
parser.add_argument('--resume', action='store_true', help='resume checkpoint or not')
parser.add_argument('--checkpoint', type=str, default=None, help='optional reload model path')
parser.add_argument('--save_dir', type=str, default='model', help='save model path')

# dataset settings
parser.add_argument('--train_dir', type=str, default='/space_sdd/InteriorNet', help='training dataset')
parser.add_argument('--train_method', type=str, default='sharpnet_pred')
parser.add_argument('--val_dir', type=str, default='/space_sdd/ibims', help='testing dataset')
parser.add_argument('--val_method', type=str, default='sharpnet')

opt = parser.parse_args()
print(opt)
# ========================================================== #


# =================CREATE DATASET=========================== #
dataset_train = InteriorNet(opt.train_dir, method_name=opt.train_method)
dataset_val = Ibims(opt.val_dir, opt.val_method)

train_loader = DataLoader(dataset_train, batch_size=opt.batch_size, shuffle=True, num_workers=opt.workers, drop_last=True)
val_loader = DataLoader(dataset_val, batch_size=1, shuffle=False, num_workers=opt.workers)
# ========================================================== #


# ================CREATE NETWORK AND OPTIMIZER============== #
net = UNet() if opt.model == 'unet' else YNet()
net.apply(kaiming_init)

optimizer = optim.Adam(net.parameters(), lr=opt.lr)
lrScheduler = optim.lr_scheduler.MultiStepLR(optimizer, [opt.step], gamma=0.1)

if opt.resume:
    start_epoch = load_checkpoint(net, optimizer, opt.checkpoint)
else:
    start_epoch = 0

net.cuda()
gamma = create_gamma_matrix(480, 640, 600, 600)
gamma = torch.from_numpy(gamma).float().cuda()
# ========================================================== #


# =============DEFINE stuff for logs ======================= #
result_path = os.path.join(os.getcwd(), opt.save_dir, 'session_{}_{}'.format(opt.model, opt.session))
if not os.path.exists(result_path):
    os.makedirs(result_path)
logname = os.path.join(result_path, 'train_log.txt')
with open(logname, 'a') as f:
    f.write(str(opt) + '\n')
    f.write('training set: ' + str(len(dataset_train)) + '\n')
    f.write('validation set: ' + str(len(dataset_val)) + '\n\n')
# ========================================================== #


# =================== DEFINE TRAIN ========================= #
def train(data_loader, net, optimizer):
    net.train()
    end = time.time()
    for i, data in enumerate(data_loader):
        # load data and label
        depth_gt, depth_coarse, occlusion, normal = data
        depth_gt, depth_coarse, occlusion, normal = depth_gt.cuda(), depth_coarse.cuda(), occlusion.cuda(), normal.cuda()

        # forward pass
        depth_pred = net(occlusion, depth_coarse)

        # compute losses and update the meters
        loss_depth_gt = berhu_loss(depth_pred, depth_gt) + spatial_gradient_loss(depth_pred, depth_gt)
        loss_depth_occ = occlusion_aware_loss(depth_pred, occlusion, normal, gamma, opt.linear, 15. / 1000, 1)
        loss = opt.alpha_depth * loss_depth_gt + opt.alpha_occ * loss_depth_occ
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # measure bacth time
        batch_time = time.time() - end
        end = time.time()

        if i % opt.print_freq == 0:
            print("\tEpoch {} --- Iter [{}/{}] Train loss: {:.3f} + {:.3f} || Batch time: {:.3f}".format(
                  epoch, i + 1, len(data_loader), opt.alpha_depth * loss_depth_gt.item(),
                  opt.alpha_occ * loss_depth_occ.item(), batch_time))
# ========================================================== #


# ===================== DEFINE VAL ========================= #
def val(data_loader, net):
    # Initialize global and geometric errors ...
    num_samples = len(data_loader)
    rms     = np.zeros(num_samples, np.float32)
    log10   = np.zeros(num_samples, np.float32)
    abs_rel = np.zeros(num_samples, np.float32)
    sq_rel  = np.zeros(num_samples, np.float32)
    thr1    = np.zeros(num_samples, np.float32)
    thr2    = np.zeros(num_samples, np.float32)
    thr3    = np.zeros(num_samples, np.float32)

    dbe_acc = np.zeros(num_samples, np.float32)
    dbe_com = np.zeros(num_samples, np.float32)

    dde_0 = np.zeros(num_samples, np.float32)
    dde_m = np.zeros(num_samples, np.float32)
    dde_p = np.zeros(num_samples, np.float32)

    net.eval()
    with torch.no_grad():
        for i, data in enumerate(data_loader):
            # load data and label
            depth_gt, depth_coarse, occlusion, edge = data
            depth_gt, depth_coarse, occlusion = depth_gt.cuda(), depth_coarse.cuda(), occlusion.cuda()

            # forward pass
            depth_pred = net(occlusion, depth_coarse).clamp(1e-9)

            # mask out invalid depth values
            valid_mask = (depth_gt != 0).float()
            gt_valid = depth_gt * valid_mask
            pred_valid = depth_pred * valid_mask

            # get numpy array from torch tensor
            gt = gt_valid.squeeze().cpu().numpy()
            pred = pred_valid.squeeze().cpu().numpy()
            edge = edge.numpy()

            gt_vec = gt.flatten()
            pred_vec = pred.flatten()

            abs_rel[i], sq_rel[i], rms[i], log10[i], thr1[i], thr2[i], thr3[i] = compute_global_errors(gt_vec, pred_vec)
            dbe_acc[i], dbe_com[i], est_edges = compute_depth_boundary_error(edge, pred)
            dde_0[i], dde_m[i], dde_p[i] = compute_directed_depth_error(gt_vec, pred_vec, 3.0)

    return abs_rel, sq_rel, rms, log10, thr1, thr2, thr3, dbe_acc, dbe_com, dde_0, dde_m, dde_p
# ========================================================== #


# =============BEGIN OF THE LEARNING LOOP=================== #
# initialization
abs_rel, sq_rel, rms, log10, thr1, thr2, thr3, dbe_acc, dbe_com, dde_0, dde_m, dde_p = val(val_loader, net)
print('############ Global Error Metrics #################')
print('rel    = ',  np.nanmean(abs_rel))
print('log10  = ',  np.nanmean(log10))
print('rms    = ',  np.nanmean(rms))
print('thr1   = ',  np.nanmean(thr1))
print('thr2   = ',  np.nanmean(thr2))
print('thr3   = ',  np.nanmean(thr3))
print('############ Depth Boundary Error Metrics #################')
print('dbe_acc = ',  np.nanmean(dbe_acc))
print('dbe_com = ',  np.nanmean(dbe_com))
print('############ Directed Depth Error Metrics #################')
print('dde_0  = ',  np.nanmean(dde_0)*100.)
print('dde_m  = ',  np.nanmean(dde_m)*100.)
print('dde_p  = ',  np.nanmean(dde_p)*100.)

best_rms = np.nanmean(rms)

for epoch in range(start_epoch, opt.epoch):
    # update learning rate
    lrScheduler.step(epoch=epoch)

    # train
    train(train_loader, net, optimizer)    

    # valuate
    abs_rel, sq_rel, rms, log10, thr1, thr2, thr3, dbe_acc, dbe_com, dde_0, dde_m, dde_p = val(val_loader, net)

    # log testing reults
    with open(logname, 'a') as f:
        f.write('Results for {} epoch:\n'.format(epoch))
        f.write('############ Global Error Metrics #################\n')
        f.write('rel    =  {:.3f}\n'.format(np.nanmean(abs_rel)))
        f.write('log10  =  {:.3f}\n'.format(np.nanmean(log10)))
        f.write('rms    =  {:.3f}\n'.format(np.nanmean(rms)))
        f.write('thr1   =  {:.3f}\n'.format(np.nanmean(thr1)))
        f.write('thr2   =  {:.3f}\n'.format(np.nanmean(thr2)))
        f.write('thr3   =  {:.3f}\n'.format(np.nanmean(thr3)))
        f.write('############ Depth Boundary Error Metrics #################\n')
        f.write('dbe_acc = {:.3f}\n'.format(np.nanmean(dbe_acc)))
        f.write('dbe_com = {:.3f}\n'.format(np.nanmean(dbe_com)))
        f.write('############ Directed Depth Error Metrics #################\n')
        f.write('dde_0  = {:.3f}\n'.format(np.nanmean(dde_0) * 100.))
        f.write('dde_m  = {:.3f}\n'.format(np.nanmean(dde_m) * 100.))
        f.write('dde_p  = {:.3f}\n\n'.format(np.nanmean(dde_p) * 100.))

    # update best_rms and save checkpoint
    save_checkpoint({
        'epoch': epoch,
        'model': net.state_dict(),
        'optimizer': optimizer.state_dict()
    }, os.path.join(result_path, 'checkpoint_{}.pth'.format(epoch)))
