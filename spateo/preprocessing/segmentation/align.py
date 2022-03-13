"""Functions to refine staining and RNA alignments.
"""
import math
from typing import List, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from anndata import AnnData
from kornia.geometry.transform import thin_plate_spline as tps
from tqdm import tqdm
from typing_extensions import Literal

from ...configuration import SKM
from ...errors import PreprocessingError


class AlignmentRefiner(nn.Module):
    def __init__(self, reference: np.ndarray, to_align: np.ndarray):
        if reference.dtype != np.dtype(bool) or to_align.dtype != np.dtype(bool):
            raise PreprocessingError("`AlignmentRefiner` only supports boolean arrays.")
        super().__init__()
        self.reference = torch.tensor(reference)[None][None].float()
        self.to_align = torch.tensor(to_align)[None][None].float()
        self.weight = torch.tensor(
            np.where(reference, reference.size / (2 * reference.sum()), reference.size / (2 * (~reference).sum()))
        )[None][None]
        self.__optimizer = None
        self.history = {}

    def loss(self, pred):
        return torch.sum(self.weight * (pred - self.reference) ** 2) / self.weight.numel()

    def optimizer(self):
        if self.__optimizer is None:
            self.__optimizer = torch.optim.Adam(self.parameters())
        return self.__optimizer

    def forward(self):
        return self.transform(self.to_align, self.get_params(True), train=True)

    def train(self, n_epochs: int = 100):
        optimizer = self.optimizer()

        with tqdm(total=n_epochs) as pbar:
            for i in range(n_epochs):
                pred = self()
                loss = self.loss(pred)
                self.history.setdefault("loss", []).append(loss.item())

                pbar.set_description(f"Loss {loss.item():.4f}")
                pbar.update(1)

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

    def get_params(self, train=False):
        raise NotImplementedError()


class NonRigidAlignmentRefiner(AlignmentRefiner):
    """Pytorch module to refine alignment between two images by evaluating the
    thin-plate-spline (TPS) for non-rigid alignment.
    Performs Autograd on the displacement matrix between source and destination
    points.
    """

    def __init__(self, reference: np.ndarray, to_align: np.ndarray, binsize: int = 1000):
        super().__init__(reference, to_align)
        self.src_points = torch.cartesian_prod(
            torch.linspace(-1, 1, math.ceil(to_align.shape[1] / binsize)),
            torch.linspace(-1, 1, math.ceil(to_align.shape[0] / binsize)),
        )
        self.displacement = nn.Parameter(torch.zeros(self.src_points.shape))

    def get_params(self, train=False):
        src_points, displacement = self.src_points, self.displacement
        if not train:
            src_points = src_points.detach().numpy()
            displacement = displacement.detach().numpy()
        return dict(src_points=src_points, displacement=displacement)

    @staticmethod
    def transform(x, params, train=False):
        """This method should be used when applying the learned affine
        transformation to an arbitrary image.
        """
        src_points, displacement = params["src_points"], params["displacement"]
        dst_points = src_points + displacement
        if not train:
            src_points = torch.tensor(src_points).float()
            dst_points = torch.tensor(dst_points).float()
            x = torch.tensor(x)[None][None].float()
        dst_points = dst_points.unsqueeze(0)
        src_points = src_points.unsqueeze(0)
        kernel_weights, affine_weights = tps.get_tps_transform(dst_points, src_points)
        t = tps.warp_image_tps(x, src_points, kernel_weights, affine_weights).squeeze()
        return t if train else t.detach().numpy()


class RigidAlignmentRefiner(AlignmentRefiner):
    """Pytorch module to refine alignment between two images.
    Performs Autograd on the affine transformation matrix.
    """

    def __init__(self, reference: np.ndarray, to_align: np.ndarray, theta: Optional[np.ndarray] = None):
        super().__init__(reference, to_align)
        self.reference = torch.tensor(reference)[None][None].float()
        self.to_align = torch.tensor(to_align)[None][None].float()

        # Affine matrix
        if theta is not None:
            self.theta = nn.Parameter(torch.tensor(theta))
        else:
            self.theta = nn.Parameter(
                torch.tensor(
                    [
                        [1.0, 0.0, 0.0],
                        [0.0, 1.0, 0.0],
                    ]
                )
            )

    @staticmethod
    def transform(x, params, train=False):
        """This method should be used when applying the learned affine
        transformation to an arbitrary image.
        """
        theta = params["theta"]
        if not train:
            theta = torch.tensor(theta).float()
            x = torch.tensor(x)[None][None].float()
        grid = F.affine_grid(theta.unsqueeze(0), x.size(), align_corners=False)
        t = F.grid_sample(x, grid, align_corners=False)
        return t if train else t.detach().numpy()

    def get_params(self, train=False):
        theta = self.theta
        if not train:
            theta = theta.detach().numpy()
        return dict(theta=theta)


MODULES = {"rigid": RigidAlignmentRefiner, "non-rigid": NonRigidAlignmentRefiner}


def refine_alignment(
    adata: AnnData,
    stain_layer: str = SKM.STAIN_LAYER_KEY,
    rna_layer: str = SKM.UNSPLICED_LAYER_KEY,
    mode: Literal["rigid", "non-rigid"] = "rigid",
    n_epochs: int = 100,
    transform_layers: Optional[List[str]] = None,
    **kwargs,
):
    """Refine the alignment between the staining image and RNA coordinates.

    There are often small misalignments between the staining image and RNA, which
    results in incorrect aggregation of pixels into cells based on staining.
    This function attempts to refine these alignments based on the staining and
    (unspliced) RNA masks.

    Args:
        adata: Input Anndata
        stain_layer: Layer containing staining image. First will look for layer
            `{stain_layer}_mask`. Otherwise, this will be taken as a literal.
        rna_layer: Layer containing (unspliced) RNA. First, will look for layer
            `{rna_layer}_mask`. Otherwise, this will be taken as a literal.
        n_epochs: Number of epochs to run optimization
        transform_layers: Layers to transform and overwrite inplace.
        **kwargs: Additional keyword arguments to pass to the Pytorch module.
    """
    if mode not in MODULES.keys():
        raise PreprocessingError('`mode` must be one of "rigid" and "non-rigid"')

    layer = SKM.gen_new_layer_key(stain_layer, SKM.MASK_SUFFIX)
    if layer not in adata.layers:
        layer = stain_layer
    stain_mask = SKM.select_layer_data(adata, layer)

    layer = SKM.gen_new_layer_key(rna_layer, SKM.MASK_SUFFIX)
    if layer not in adata.layers:
        layer = rna_layer
    rna_mask = SKM.select_layer_data(adata, layer)

    module = MODULES[mode]
    aligner = module(rna_mask, stain_mask, **kwargs)
    aligner.train(n_epochs)

    params = aligner.get_params()
    SKM.set_uns_spatial_attribute(adata, SKM.UNS_SPATIAL_ALIGNMENT_KEY, params)

    if transform_layers:
        for layer in transform_layers:
            data = SKM.select_layer_data(adata, layer)
            transformed = aligner.transform(data, params)
            if data.dtype == np.dtype(bool):
                transformed = transformed > 0.5
            SKM.set_layer_data(adata, layer, transformed)
