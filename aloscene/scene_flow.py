import aloscene
from aloscene import Depth, CameraIntrinsic, Flow, Mask
from typing import Union
import numpy as np
import torch


def load_scene_flow(path: str) -> torch.Tensor:
    with open(path, "rb") as file:
        data = np.load(file)
        return torch.from_numpy(data)


def create_point_3d(depth: Depth, intrinsic: CameraIntrinsic) -> np.ndarray:
    points3d = depth.as_points3d(camera_intrinsic=intrinsic).cpu().numpy()
    mask_points = np.isfinite(points3d).all(1)
    points3d = points3d[mask_points]
    return points3d


class SceneFlow(aloscene.tensors.SpatialAugmentedTensor):
    """
    Scene flow map

    Parameters
    ----------
    x : str
        load scene flow from a numpy file
    """

    @staticmethod
    def __new__(cls, x, occlusion: Mask = None, *args, names=("C", "H", "W"), **kwargs):
        if isinstance(x, str):
            # load flow from path
            x = load_scene_flow(x)
            names = ("C", "H", "W")

        tensor = super().__new__(cls, x, *args, names=names, **kwargs)
        tensor.add_child("occlusion", occlusion, align_dim=["B", "T"], mergeable=True)
        return tensor

    def __init__(self, x, *args, **kwargs):
        super().__init__(x)

    @classmethod
    def from_optical_flow(
        cls,
        optical_flow: Flow,
        depth: Depth,
        next_depth: Depth,
        intrinsic: CameraIntrinsic,
    ):
        """Create scene flow from optical flow, depth a T, depth at T + 1 and the intrinsic

        Parameters
        ----------
        optical flow: aloscene.Flow
            The optical flow at T.
        depth: aloscene.Depth
            The depth at T."
        next_depth: aloscene.Depth
            The depth at T + 1
        intrinsic : aloscene.CameraIntrinsic
            The intrinsic of the image at T.
        """
        start_vector = create_point_3d(depth, intrinsic)
        new_coord = np.mgrid[0 : depth.H : 1, 0 : depth.W : 1].reshape(2, -1).T
        new_coord = np.reshape(new_coord, (depth.H, depth.W, 2))
        new_coord = np.round(new_coord + optical_flow.as_numpy(), 0).astype(int)
        end_vector = create_point_3d(next_depth, intrinsic)

        mask: np.ndarray = optical_flow.occlusion.numpy()

        result = np.zeros((depth.H, depth.W, 3))
        for height in range(depth.H):
            for width in range(depth.W):
                if mask[height][width] == 1:
                    result[height][width] = (
                        end_vector[
                            new_coord[height][width][0] * 640
                            + new_coord[height][width][1]
                        ]
                        - start_vector[height * 640 + width]
                    )
        result = torch.from_numpy(result)
        cls = cls(result)
        cls.append_occlusion(optical_flow.occlusion.clone(), "occlusion")
        return cls

    def append_occlusion(self, occlusion: Mask, name: Union[str, None] = None):
        """Attach an occlusion mask to the scene flow.

        Parameters
        ----------
        occlusion: aloscene.Mask
            Occlusion mask to attach to the Scene Flow
        name: str
            If none, the occlusion mask will be attached without name (if possible). Otherwise if no other unnamed
            occlusion mask are attached to the scene flow, the mask will be added to the set of mask.
        """
        self._append_child("occlusion", occlusion, name)
