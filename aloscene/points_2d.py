from __future__ import annotations

from torchvision.io.image import read_image
import torch
from torch import Tensor
from torch._C import device
import torchvision

from typing import *
import numpy as np
import cv2

import aloscene
from aloscene.renderer import View
from aloscene.labels import Labels
import torchvision
from torchvision.ops.boxes import nms
from aloscene.renderer import View, put_adapative_cv2_text, adapt_text_size_to_frame


class Points2D(aloscene.tensors.AugmentedTensor):
    """Point2D Tensor."""

    FORMATS = ["xy", "yx"]

    @staticmethod
    def __new__(
        cls,
        x,
        points_format: str,
        absolute: bool,
        labels: Union[dict, Labels] = None,
        frame_size=None,
        names=("N", None),
        *args,
        **kwargs,
    ):
        tensor = super().__new__(cls, x, *args, names=names, **kwargs)

        # Add label
        tensor.add_label("labels", labels, align_dim=["N"], mergeable=True)

        if points_format not in Points2D.FORMATS:
            raise Exception(
                "Point2d:Format `{}` not supported. Cound be one of {}".format(tensor.points_format, Points2D.FORMATS)
            )
        tensor.add_property("points_format", points_format)
        tensor.add_property("absolute", absolute)
        tensor.add_property("padded_size", None)

        if absolute and frame_size is None:
            raise Exception("If the boxes format are absolute, the `frame_size` must be set")
        assert frame_size is None or (isinstance(frame_size, tuple) and len(frame_size) == 2)
        tensor.add_property("frame_size", frame_size)

        return tensor

    def __init__(self, x, *args, **kwargs):
        super().__init__(x)

    def append_labels(self, labels: Labels, name: str = None):
        """Attach a set of labels to the boxes.

        Parameters
        ----------
        labels: aloscene.Labels
            Set of labels to attached to the frame
        name: str
            If none, the label will be attached without name (if possible). Otherwise if no other unnamed
            labels are attached to the frame, the labels will be added to the set of labels.
        """
        self._append_label("labels", labels, name)

    @staticmethod
    def xyxy(tensor):
        """Get a new Point2d Tensor with boxes following this format:
        [x_center, y_center, width, height]. Could be relative value (betwen 0 and 1)
        or absolute value based on the current Tensor representation.
        """
        if tensor.points_format == "xcyc":
            return tensor
        elif tensor.points_format == "xyxy":
            # Convert from xyxy to xcyc
            labels = tensor.drop_labels()
            xcyc_boxes = torch.cat(
                [tensor[:, :2] + ((tensor[:, 2:] - tensor[:, :2]) / 2), (tensor[:, 2:] - tensor[:, :2])], dim=1
            )
            xcyc_boxes.points_format = "xcyc"
            xcyc_boxes.set_labels(labels)
            tensor.set_labels(labels)
            return xcyc_boxes
        elif tensor.points_format == "yxyx":
            # Convert from yxyx to xcyc
            labels = tensor.drop_labels()
            tensor = tensor.rename_(None)
            xcyc_boxes = torch.cat(
                [
                    tensor[:, :2].flip([1]) + ((tensor[:, 2:].flip([1]) - tensor[:, :2].flip([1])) / 2),
                    (tensor[:, 2:].flip([1]) - tensor[:, :2].flip([1])),
                ],
                dim=1,
            )
            tensor.reset_names()
            xcyc_boxes.reset_names()
            xcyc_boxes.points_format = "xcyc"
            xcyc_boxes.set_labels(labels)
            tensor.set_labels(labels)
            return xcyc_boxes
        else:
            raise Exception(f"Point2d:Do not know mapping from {tensor.points_format} to xcyc")

    def xy(self):
        """Get a new Point2d Tensor with boxes following this format:
        [x, y]. Could be relative value (betwen 0 and 1)
        or absolute value based on the current Tensor representation.
        """
        tensor = self.clone()
        if tensor.points_format == "xy":
            return tensor
        elif tensor.points_format == "yx":
            tensor = tensor[:, ::-1]
            tensor.points_format = "xy"
            return tensor

    def yx(self):
        """Get a new Point2d Tensor with boxes following this format:
        [y, x]. Could be relative value (betwen 0 and 1)
        or absolute value based on the current Tensor representation.
        """
        tensor = self.clone()
        if tensor.points_format == "yx":
            return tensor
        elif tensor.points_format == "xy":
            tensor = tensor[:, ::-1]
            tensor.points_format = "yx"
            return tensor

    def abs_pos(self, frame_size) -> Points2D:
        """Get a new Point2d Tensor with absolute position
        relative to the given `frame_size`.
        """
        tensor = self.clone()

        # Back to relative before to get the absolute pos
        if tensor.absolute and frame_size != tensor.frame_size:

            if tensor.points_format == "xy":
                mul_tensor = torch.tensor([[frame_size[1], frame_size[0]]], device=self.device)
            else:
                mul_tensor = torch.tensor([[frame_size[0], frame_size[1]]], device=self.device)

            tensor.rel_pos()
            tensor = tensor.mul(mul_tensor)
            tensor.frame_size = frame_size
            tensor.absolute = True

        elif tensor.absolute and frame_size == tensor.frame_size:
            return tensor
        else:

            if tensor.points_format == "xy":
                mul_tensor = torch.tensor([[frame_size[1], frame_size[0]]], device=self.device)
            else:
                mul_tensor = torch.tensor([[frame_size[0], frame_size[1]]], device=self.device)

            tensor = tensor.mul(mul_tensor)
            tensor.frame_size = frame_size
            tensor.absolute = True

            return tensor

    def rel_pos(self):
        """Get a new Point2d Tensor with relative position
        based on the current frame_size
        """
        tensor = self.clone()

        # Back to relative before to get the absolute pos
        if tensor.absolute:
            if tensor.points_format == "xy":
                div_tensor = torch.tensor([[self.frame_size[1], self.frame_size[0]]], device=self.device)
            else:
                div_tensor = torch.tensor([[self.frame_size[0], self.frame_size[1]]], device=self.device)
            tensor = tensor.div(div_tensor)
            tensor.absolute = False
            return tensor
        else:
            return tensor

    def get_with_format(self, points_format):
        """Set boxes into the desired format (Inplace operation)"""
        if points_format == "xy":
            return self.xy()
        elif points_format == "yx":
            return self.yx()
        else:
            raise Exception(f"desired points_format {points_format} is not handle")

    GLOBAL_COLOR_SET = np.random.uniform(0, 1, (300, 3))

    def get_view(self, frame: Tensor = None, size: tuple = None, labels_set: str = None, **kwargs):
        """Create a view of the boxes a frame

        Parameters
        ----------
        frame: aloscene.Frame
            Tensor of type Frame to display the boxes on. If the frameis None, a frame will be create on the fly.
        size: (tuple)
            (height, width) Desired size of the view. None by default
        labels_set: str
            If provided, the boxes will rely on this label set to display the boxes color. If labels_set
            is not provie while the boxes have multiple labels set, the boxes will be display with the same colors.
        """
        from aloscene import Frame

        if frame is not None:
            if len(frame.shape) > 3:
                raise Exception(f"Expect image of shape c,h,w. Found image with shape {frame.shape}")
            assert isinstance(frame, Frame)
        else:
            size = self.frame_size if self.absolute else (300, 300)
            frame = torch.zeros(3, int(size[0]), int(size[1]))
            frame = Frame(frame, names=("C", "H", "W"), normalization="01")

        if self.padded_size is not None:
            points_abs = self.fit_to_padded_size()
            points_abs = points_abs.xy().abs_pos(frame.HW)
        else:
            points_abs = self.xy().abs_pos(frame.HW)

        # Get an imave with values between 0 and 1
        frame_size = (frame.H, frame.W)
        frame = frame.norm01().cpu().rename(None).permute([1, 2, 0]).detach().contiguous().numpy()
        # Draw bouding boxes

        # Try to retrieve the associated label ID (if any)
        labels = points_abs.labels if isinstance(points_abs.labels, aloscene.Labels) else [None] * len(points_abs)
        if labels_set is not None and not isinstance(points_abs.labels, dict):
            raise Exception(
                f"Trying to display a set of boxes labels ({labels_set}) while the boxes do not have multiple set of labels"
            )
        elif labels_set is not None and isinstance(points_abs.labels, dict) and labels_set not in points_abs.labels:
            raise Exception(
                f"Trying to display a set of boxes labels ({labels_set}) while the boxes no not have this set. Avaiable set ("
                + [key for key in points_abs.labels]
                + ") "
            )
        elif labels_set is not None:
            labels = points_abs.labels[labels_set]
            assert labels.encoding == "id"

        size, _ = adapt_text_size_to_frame(1.0, frame_size)
        for box, label in zip(points_abs, labels):
            box = box.round()
            x1, y1 = box.as_tensor()
            color = (0, 1, 0)
            if label is not None:
                color = self.GLOBAL_COLOR_SET[int(label) % len(self.GLOBAL_COLOR_SET)]

                put_adapative_cv2_text(
                    frame,
                    frame_size,
                    str(int(label)),
                    pos_x=int(x1) + 10,
                    pos_y=int(y1) + 10,
                    color=color,
                    square_background=False,
                )

            cv2.circle(frame, (int(x1), int(y1)), int(size * 5), color, 2)
        # Return the view to display
        return View(frame, **kwargs)

    def _hflip(self, **kwargs):
        """Flip points horizontally"""
        points = self.clone()

        absolute = points.absolute
        frame_size = points.frame_size
        points_format = points.points_format

        # Transform to relative position, set format
        points = points.rel_pos().xy()

        # Flip horizontally
        points = torch.tensor([1.0, 0.0]) - points
        points.mul_(torch.tensor([1.0, -1.0]))

        # Put back the instance into the same state as before
        if absolute:
            points = points.abs_pos(frame_size)
        points = points.get_with_format(points_format)

        return points

    def _resize(self, size, **kwargs):
        """Resize Point2d, but not their labels

        Parameters
        ----------
        size : tuple of float
            target size (H, W) in relative coordinates between 0 and 1

        Returns
        -------
        points : aloscene.Point2d
            resized points
        """
        points = self.clone()
        # no modification needed for relative coordinates
        if not points.absolute:
            return points
        else:
            abs_size = tuple(s * fs for s, fs in zip(size, points.frame_size))
            return points.abs_pos(abs_size)

    def _crop(self, H_crop: tuple, W_crop: tuple, **kwargs):
        """Crop Boxes with the given relative crop

        Parameters
        ----------
        H_crop: tuple
            (start, end) between 0 and 1
        W_crop: tuple
            (start, end) between 0 and 1

        Returns
        -------
        cropped_boxes2d sa_tensor: aloscene.Point2d
            cropped_boxes2d Point2d
        """
        absolute = self.absolute
        frame_size = self.frame_size
        points_format = self.points_format

        # Get a new set of bbox
        n_points = self.abs_pos((100, 100)).xy()

        # Retrieve crop coordinates
        h = (H_crop[1] - H_crop[0]) * 100
        w = (W_crop[1] - W_crop[0]) * 100
        max_size = torch.as_tensor([w, h], dtype=torch.float32)
        x, y = W_crop[0] * 100, H_crop[0] * 100

        # Crop boxes
        cropped_points = n_points - torch.as_tensor([x, y])

        cropped_points_filter = (cropped_points >= 0).as_tensor() & (cropped_points < max_size).as_tensor()
        cropped_points_filter = cropped_points_filter[:, 0] & cropped_points_filter[:, 1]
        cropped_points = cropped_points[cropped_points_filter]

        cropped_points.frame_size = (h, w)
        cropped_points = cropped_points.rel_pos()

        # Put back the instance into the same state as before
        if absolute:
            cropped_points = cropped_points.abs_pos(frame_size)
        cropped_points = cropped_points.get_with_format(points_format)

        return cropped_points

    def fit_to_padded_size(self):
        """If the set of Boxes did not get padded by the pad operation,
        this method wil padd the boxes to the real padded size.

        Returns
        -------
        padded_boxes2d sa_tensor: aloscene.Point2d
            padded_boxes2d Point2d
        """
        if self.padded_size is None:
            raise Exception("Trying to fit to padded size without any previous stored padded_size.")

        if not self.absolute:
            frame_size = (100, 100)  # Virtual frame size
        else:
            frame_size = self.frame_size

        offset_x = (0, self.padded_size[1] / frame_size[1])
        offset_y = (0, self.padded_size[0] / frame_size[0])

        if not self.absolute:
            points = self.abs_pos((100, 100))
            points.frame_size = (100 * offset_y[1], 100 * offset_x[1])
            points = points.rel_pos()
        else:
            points = self.clone()
            points.frame_size = (
                round(points.frame_size[0] * (offset_y[1])),
                round(points.frame_size[1] * (offset_x[1])),
            )

        points.padded_size = None

        return points

    def _pad(self, offset_y: tuple, offset_x: tuple, pad_boxes: bool = False, **kwargs):
        """Pad the set of boxes based on the given offset

        Parameters
        ----------
        offset_y: tuple
            (percentage top_offset, percentage bottom_offset) Percentage based on the previous size
        offset_x: tuple
            (percentage left_offset, percentage right_offset) Percentage based on the previous size
        pad_boxes: bool
            By default, the boxes are not changed when we pad the frame. Therefore the boxes still
            encode the position of the boxes based on the frame before the padding. This is usefull in some
            cases, like in transformer architecture where the padded ares are masked. Therefore, the transformer
            do not "see" the padded part of the frames.

        Returns
        -------
        boxes2d: aloscene.Point2d
            padded_boxes2d Point2d or unchange Point2d (if pad_boxes is False)
        """
        # TODO: pad_boxes. Must find a more generic approached for this
        assert offset_y[0] == 0 and offset_x[0] == 0, "Not handle yet"

        if not pad_boxes:

            n_points = self.clone()

            if n_points.padded_size is not None:
                pr_frame_size = n_points.padded_size
            elif n_points.padded_size is None and n_points.absolute:
                pr_frame_size = self.frame_size
            else:
                pr_frame_size = (100, 100)

            n_points.padded_size = (pr_frame_size[0] * (1.0 + offset_y[1]), pr_frame_size[1] * (1.0 + offset_x[1]))
            return n_points

        if self.padded_size is not None:
            raise Exception("Padding with pad_boxes True while padded_size is not None is not supported Yet.")

        if not self.absolute:
            points = self.abs_pos((100, 100))
            points.frame_size = (100 * (1.0 + offset_y[1]), 100 * (1.0 + offset_x[1]))
            points = points.rel_pos()
        else:
            points = self.clone()
            points.frame_size = (
                points.frame_size[0] * (offset_y[1] + 1.0),
                points.frame_size[1] * (offset_x[1] + 1.0),
            )

        return points

    def _spatial_shift(self, shift_y: float, shift_x: float, **kwargs):
        raise Exception("Not handle by points 2D")

    def as_points(self, points):
        n_points = self.clone()

        if points.absolute and not n_points.absolute:
            n_points = n_points.abs_pos(points.frame_size)
        elif not points.absolute and n_points.absolute:
            n_points = n_points.rel_pos()

        n_points = n_points.get_with_format(points.points_format)

        if points.padded_size is not None:
            n_points.padded_size = points.padded_size

        return n_points

    def remove_padding(self):
        n_points = self.clone()
        n_points.padded_size = None
        return n_points
