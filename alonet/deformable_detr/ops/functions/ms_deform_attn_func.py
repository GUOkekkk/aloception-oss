# ------------------------------------------------------------------------------------------------
# Deformable DETR
# Copyright (c) 2020 SenseTime. All Rights Reserved.
# Licensed under the Apache License, Version 2.0 [see LICENSE for details]
# ------------------------------------------------------------------------------------------------
# Modified from https://github.com/chengdazhi/Deformable-Convolution-V2-PyTorch/tree/pytorch_1.0.0
# ------------------------------------------------------------------------------------------------

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

import os
import subprocess
import torch
import torch.nn.functional as F
from torch.autograd import Function
from torch.autograd.function import once_differentiable
from alonet import ALONET_ROOT


def load_ops():
    ops_dir = os.path.join(ALONET_ROOT, "deformable_detr/ops/build/")
    lib_dir = [dirname for dirname in os.listdir(ops_dir) if "lib" in dirname][0]
    module_path = os.path.join(ops_dir, lib_dir, "MultiScaleDeformableAttention.so")
    torch.ops.load_library(module_path)


def load_MultiScaleDeformableAttention():
    """
    This function must be call once before using MSDeformAttnFunction.
    If a nn.Module class want to use MSDeformAttnFunction,
    it can call this function in its __init__

    Load Multiscale Deformable Attention operation.
    If this custom op is not already built,
    this function will execute the build script.
    """

    try:
        load_ops()
    except:
        print("Building ms_deform_attn operation for PyTorch ...")
        make_file = os.path.join(ALONET_ROOT, "deformable_detr/ops/make.sh")
        subprocess.call(["sh", make_file, ALONET_ROOT])
        load_ops()


class MSDeformAttnFunction(Function):
    @staticmethod
    def forward(
        ctx, value, value_spatial_shapes, value_level_start_index, sampling_locations, attention_weights, im2col_step
    ):
        ctx.im2col_step = im2col_step
        output = torch.ops.alonet_custom.ms_deform_attn_forward(
            value,
            value_spatial_shapes,
            value_level_start_index,
            sampling_locations,
            attention_weights,
            ctx.im2col_step,
        )
        ctx.save_for_backward(
            value, value_spatial_shapes, value_level_start_index, sampling_locations, attention_weights
        )
        return output

    @staticmethod
    @once_differentiable
    def backward(ctx, grad_output):
        value, value_spatial_shapes, value_level_start_index, sampling_locations, attention_weights = ctx.saved_tensors
        grad_value, grad_sampling_loc, grad_attn_weight = torch.ops.alonet_custom.ms_deform_attn_backward(
            value,
            value_spatial_shapes,
            value_level_start_index,
            sampling_locations,
            attention_weights,
            grad_output,
            ctx.im2col_step,
        )

        return grad_value, None, None, grad_sampling_loc, grad_attn_weight, None


def ms_deform_attn_core_pytorch(value, value_spatial_shapes, sampling_locations, attention_weights):
    # for debug and test only,
    # need to use cuda version instead
    N_, S_, M_, D_ = value.shape
    _, Lq_, M_, L_, P_, _ = sampling_locations.shape
    value_list = value.split([H_ * W_ for H_, W_ in value_spatial_shapes], dim=1)
    sampling_grids = 2 * sampling_locations - 1
    sampling_value_list = []
    for lid_, (H_, W_) in enumerate(value_spatial_shapes):
        # N_, H_*W_, M_, D_ -> N_, H_*W_, M_*D_ -> N_, M_*D_, H_*W_ -> N_*M_, D_, H_, W_
        value_l_ = value_list[lid_].flatten(2).transpose(1, 2).reshape(N_ * M_, D_, H_, W_)
        # N_, Lq_, M_, P_, 2 -> N_, M_, Lq_, P_, 2 -> N_*M_, Lq_, P_, 2
        sampling_grid_l_ = sampling_grids[:, :, :, lid_].transpose(1, 2).flatten(0, 1)
        # N_*M_, D_, Lq_, P_
        sampling_value_l_ = F.grid_sample(
            value_l_, sampling_grid_l_, mode="bilinear", padding_mode="zeros", align_corners=False
        )
        sampling_value_list.append(sampling_value_l_)
    # (N_, Lq_, M_, L_, P_) -> (N_, M_, Lq_, L_, P_) -> (N_, M_, 1, Lq_, L_*P_)
    attention_weights = attention_weights.transpose(1, 2).reshape(N_ * M_, 1, Lq_, L_ * P_)
    output = (torch.stack(sampling_value_list, dim=-2).flatten(-2) * attention_weights).sum(-1).view(N_, M_ * D_, Lq_)
    return output.transpose(1, 2).contiguous()
