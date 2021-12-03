import re
import os
import time
import warnings
from tqdm import tqdm
import pandas as pd
import numpy as np
import anndata as ad
import ot
import torch
from scipy.spatial import distance_matrix
from scipy.sparse.csr import spmatrix
from scipy.sparse import csr_matrix
import pyvista as pv

def pairwise_align1(slice1, slice2, alpha=0.1, numItermax=200, numItermaxEmd=100000):
    '''
    Calculates and returns optimal alignment of two slices via CPU.

    Parameters
    ----------
        slice1: 'anndata.AnnData'
            An AnnData object.
        slice2: 'anndata.AnnData'
            An AnnData object.
        alpha: 'float' (default: 0.1)
            Trade-off parameter (0 < alpha < 1).
        numItermax: 'int' (default: 200)
            max number of iterations for cg.
        numItermaxEmd: 'int' (default: 100000)
            Max number of iterations for emd.

    Returns
    -------
        pi: 'np.array'
            alignment of spots.

    '''

    # subset for common genes
    common_genes = [value for value in slice1.var.index if value in set(slice2.var.index)]
    slice1, slice2 = slice1[:, common_genes], slice2[:, common_genes]

    # Calculate spatial distances
    DA = distance_matrix(slice1.obsm['spatial'], slice1.obsm['spatial'])
    DB = distance_matrix(slice2.obsm['spatial'], slice2.obsm['spatial'])

    # Calculate expression dissimilarity
    to_dense_array = lambda X: np.array(X.todense()) if isinstance(X, spmatrix) else X
    AX, BX = to_dense_array(slice1.X), to_dense_array(slice2.X)
    X, Y = AX + 0.01, BX + 0.01
    X ,Y = X / X.sum(axis=1, keepdims=True), Y / Y.sum(axis=1, keepdims=True)
    logX, logY = np.log(X), np.log(Y)
    X_log_X = np.matrix([np.dot(X[i], logX[i].T) for i in range(X.shape[0])])
    D = X_log_X.T - np.dot(X, logY.T)
    M = np.asarray(D)

    # init distributions
    a = np.ones((slice1.shape[0],)) / slice1.shape[0]
    b = np.ones((slice2.shape[0],)) / slice2.shape[0]

    # Run OT via CPU
    pi = ot.gromov.fused_gromov_wasserstein(M=M, C1=DA, C2=DB, p=a, q=b, loss_fun='square_loss',alpha=alpha,
                                            armijo=False, log=False,numItermax = numItermax, numItermaxEmd=numItermaxEmd)
    return pi

def pairwise_align2(slice1, slice2, alpha=0.1, numItermax=200, numItermaxEmd=100000, device=torch.device(f'cuda:0')):
    '''
    Calculates and returns optimal alignment of two slices via GPU.

    Parameters
    ----------
        slice1: 'anndata.AnnData'
            An AnnData object.
        slice2: 'anndata.AnnData'
            An AnnData object.
        alpha: 'float' (default: 0.1)
            Trade-off parameter (0 < alpha < 1).
        numItermax: 'int' (default: 200)
            max number of iterations for cg.
        numItermaxEmd: 'int' (default: 100000)
            Max number of iterations for emd.
         device: 'torch.device' (default: torch.device(f'cuda:0'))
            Equipment used to run the program.

    Returns
    -------
        pi: 'np.array'
            alignment of spots.

    '''

    # subset for common genes
    common_genes = [value for value in slice1.var.index if value in set(slice2.var.index)]
    slice1, slice2 = slice1[:, common_genes], slice2[:, common_genes]

    # Calculate spatial distances
    DA = distance_matrix(slice1.obsm['spatial'], slice1.obsm['spatial'])
    DB = distance_matrix(slice2.obsm['spatial'], slice2.obsm['spatial'])

    # Calculate expression dissimilarity
    to_dense_array = lambda X: np.array(X.todense()) if isinstance(X, spmatrix) else X
    AX, BX = to_dense_array(slice1.X), to_dense_array(slice2.X)

    X, Y = AX + 0.01, BX + 0.01
    X ,Y = X / X.sum(axis=1, keepdims=True), Y / Y.sum(axis=1, keepdims=True)
    logX, logY = np.log(X), np.log(Y)
    XlogX = np.matrix([np.dot(X[i], logX[i].T) for i in range(X.shape[0])])
    D = XlogX.T - np.dot(X, logY.T)

    # init distributions
    p = np.ones((slice1.shape[0],)) / slice1.shape[0]
    q = np.ones((slice2.shape[0],)) / slice2.shape[0]

    # Run OT via GPU
    constC, hC1, hC2 = ot.gromov.init_matrix(DA, DB, p, q, loss_fun= 'square_loss')
    constC = torch.from_numpy(constC).to(device=device)
    hC1, hC2 = torch.from_numpy(hC1).to(device=device), torch.from_numpy(hC2).to(device=device)
    DA, DB = torch.from_numpy(DA).to(device=device), torch.from_numpy(DB).to(device=device)
    M = torch.from_numpy(np.asarray(D)).to(device=device)
    p, q = torch.from_numpy(p).to(device=device), torch.from_numpy(q).to(device=device)
    G0 = p[:, None] * q[None, :]

    def f(G):
        return ot.gromov.gwloss(constC, hC1, hC2, G)

    def df(G):
        return ot.gromov.gwggrad(constC, hC1, hC2, G)

    torch.cuda.empty_cache()
    pi = ot.optim.cg(p, q, (1 - alpha) * M, alpha, f, df, G0=G0, log=False,numItermax=numItermax,
                     numItermaxEmd=numItermaxEmd, C1=DA, C2=DB, constC=constC, armijo=False)
    torch.cuda.empty_cache()

    return pi.cpu().numpy()

def slice_alignment(slicesList=None, alpha=0.1, numItermax=200, numItermaxEmd=100000,
                    device='cpu', save=None, verbose=True):
    '''
    Align all slice coordinates.

    Parameters
    ----------
        slicesList: 'list'
            An AnnData list.
        alpha: 'float' (default: 0.1)
            Trade-off parameter (0 < alpha < 1).
        numItermax: 'int' (default: 200)
            max number of iterations for cg.
        numItermaxEmd: 'int' (default: 100000)
            Max number of iterations for emd.
        device: 'str' or 'torch.device' (default: 'cpu')
            Equipment used to run the program.
        save: 'str' (default: None)
            Whether to save the data after alignment.
        verbose: 'bool' (default: True)
            Whether to print information along alignment.

    Returns
    -------
        slicesList: 'list'
            An AnnData list after alignment.

    '''

    def _log(m):
        if verbose:
            print(m)

    warnings.filterwarnings('ignore')
    startTime = time.time()

    if device == 'cpu':
        _log("************ Begin of alignment via CPU ************\n")
        piList = [
            pairwise_align1(slicesList[i],
                            slicesList[i + 1],
                            alpha=alpha,
                            numItermax=numItermax,
                            numItermaxEmd=numItermaxEmd)
            for i in tqdm(range(len(slicesList) - 1), desc=" Alignment ")
        ]
    else:
        _log("************ Begin of alignment via GPU ************\n")
        piList = [
            pairwise_align2(slicesList[i],
                            slicesList[i + 1],
                            alpha=alpha,
                            numItermax=numItermax,
                            numItermaxEmd=numItermaxEmd,
                            device=device)
            for i in tqdm(range(len(slicesList) - 1), desc=" Alignment ")
        ]

    for i in range(len(slicesList)-1):
        slice1 = slicesList[i].copy()
        slice2 = slicesList[i+1].copy()

        rawSlice1Coor, rawSlice2Coor = slice1.obsm['spatial'], slice2.obsm['spatial']
        pi = piList[i]
        slice1Coor = rawSlice1Coor - pi.sum(axis=1).dot(rawSlice1Coor)
        slice2Coor = rawSlice2Coor - pi.sum(axis=0).dot(rawSlice2Coor)
        H = slice2Coor.T.dot(pi.T.dot(slice1Coor))
        U, S, Vt = np.linalg.svd(H)
        R = Vt.T.dot(U.T)
        slice2Coor = R.dot(slice2Coor.T).T

        slice1.obsm['spatial'] = pd.DataFrame(slice1Coor, columns=['x', 'y'], index=rawSlice1Coor.index, dtype=float)
        slice2.obsm['spatial'] = pd.DataFrame(slice2Coor, columns=['x', 'y'], index=rawSlice2Coor.index, dtype=float)
        slicesList[i] = slice1
        slicesList[i+1] = slice2

    for i, slice in enumerate(slicesList):
        z = re.findall(r"_S(\d+)", slice.obs['slice_ID'][0])[0]
        slice.obs["new_x"] = slice.obsm['spatial']['x']
        slice.obs["new_y"] = slice.obsm['spatial']['y']
        slice.obs["new_z"] = slice.obsm['spatial']['z'] = float(z)
        slicesList[i] = slice

    if save != None:
        if not os.path.exists(save):
            os.mkdir(save)
        for slice in slicesList:
            subSave = os.path.join(save, f"{slice.obs['slice_ID'][0]}.h5ad")
            slice.write_h5ad(subSave)

    _log(f'************ End of alignment (It takes {round(time.time() - startTime, 2)} seconds) ************')

    return slicesList

def slice_alignment_sample(slicesList=None, frac=0.5, numItermax=200, numItermaxEmd=100000,
                           device='cpu', save=None, verbose=True, **kwargs):
    '''
    Align the slices after selecting frac*100 percent of genes.

    Parameters
    ----------
        slicesList: 'list'
            An AnnData list.
        frac: 'float' (default: 0.5)
            Fraction of gene items to return.
        numItermax: 'int' (default: 200)
            max number of iterations for cg.
        numItermaxEmd: 'int' (default: 100000)
            Max number of iterations for emd.
        device: 'str' or 'torch.device' (default: 'cpu')
            Equipment used to run the program.
        save: 'str' (default: None)
            Whether to save the data after alignment.
        verbose: 'bool' (default: True)
            Whether to print information along alignment.
        **kwargs : dict
             Parameters for slice_alignment.

    Returns
    -------
        slicesList: 'list'
            An AnnData list after alignment.
    '''

    def _select_sample(adata=None, frac=0.5):
        adata.raw = adata
        to_dense_array = lambda X: np.array(X.todense()) if isinstance(X, spmatrix) else X
        data = pd.DataFrame(to_dense_array(adata.X), columns=adata.var_names,index=adata.obs_names)
        sample_data = data.sample(frac=frac, axis=1)
        sample_data_m = csr_matrix(sample_data.values)
        newAdata = ad.AnnData(sample_data_m)
        newAdata.var_names = sample_data.columns
        newAdata.obs = adata.obs
        newAdata.obsm = adata.obsm
        newAdata.raw = adata
        return newAdata

    sampleAdataList = [
        _select_sample(adata=adata, frac=frac)
        for adata in slicesList
    ]

    regAdataList = slice_alignment(slicesList=sampleAdataList,
                                   numItermax=numItermax,
                                   numItermaxEmd=numItermaxEmd,
                                   device=device,
                                   save=save,
                                   verbose=verbose,
                                   **kwargs)

    slicesList = []
    for adata in regAdataList:
        newAdata = adata.raw.copy()
        newAdata.obs = adata.obs
        newAdata.obsm = adata.obsm
        slicesList.append(newAdata)

    return slicesList


def plot_3D(adata=None, cluster=None, colormap=None, window_size=(1024, 768), off_screen=True,
            background_color="black", font_color="white", font_size=12, cpos=("xy", "xz", "yz", "iso"),
            save=None, framerate=15, viewup=(0.5, 0.5, 1)):
    '''

    Draw a 3D image that integrates all the slices through pyvista, and you can output a png image file, or a gif image
    file, or an MP4 video file.

    Parameters
    ----------
        adata: 'anndata.AnnData'
            An Integrate all sliced AnnData object. adata.obsm['spatial'] includes x, y, z axis information, and adata.obs includes various
            clusters of information
        cluster: 'str'
            Cluster column name in adata.obs.
        colormap: 'list'
            A list of colors to override an existing colormap with a custom one.
            For example, to create a three color colormap you might specify ['green', 'red', 'blue'].
        window_size: 'tuple' (default: (1024, 768))
            Window size in pixels.
        off_screen: 'bool' (default: True)
            Whether to close the pop-up interactive window.
        background_color: 'str' (default: "black")
            The background color of the window.
        font_color: 'str' (default: "white")
            Set the font color.
        font_size: 'int' (default: 12)
            Set the font size.
        cpos: 'tuple' (default: ("xy", "xz", "yz", "iso"))
            Tuple of camera position. You can choose 4 perspectives from the following seven perspectives for drawing,
            and the last one is the main perspective. ("xy", "xz", "yz", "yx", "zx", "zy", "iso")
        save: 'str'
            Output file name. Filename should end in png, gif or mp4.
        framerate: 'int' (default: 15)
            Frames per second.The larger the framerate, the faster the rotation speed. (Used when the output file is MP4)
        viewup: 'tuple' (default: (0.5, 0.5, 1))
            In the process of generating the track path around the data scene, viewup is the normal of the track plane.

    '''

    plot_color = ["#F56867", "#FEB915", "#C798EE", "#59BE86", "#7495D3", "#D1D1D1", "#6D1A9C", "#15821E", "#3A84E6",
                  "#997273", "#787878", "#DB4C6C", "#9E7A7A", "#554236", "#AF5F3C", "#93796C", "#F9BD3F", "#DAB370",
                  "#877F6C", "#268785"]
    if colormap == None:
        plot_color = plot_color[:len(adata.obs[cluster].unique())]
        pass
    else:
        plot_color = colormap

    points = adata.obsm["spatial"].values
    grid = pv.PolyData(points)
    grid["cluster"] = adata.obs[cluster]
    volume = grid.delaunay_3d()
    surf = volume.extract_geometry()
    surf.subdivide(nsub=3, subfilter="loop", inplace=True)
    clipped = grid.clip_surface(surf)
    p = pv.Plotter(shape="3|1", off_screen=off_screen, border=True,border_color=font_color,
                   lighting="light_kit", window_size=list(window_size))
    p.background_color = background_color
    for i, _cpos in enumerate(cpos):
        p.subplot(i)
        p.add_mesh(surf, show_scalar_bar=False, show_edges=False, opacity=0.8, color="gray")
        p.add_mesh(clipped, opacity=0.8, scalars="cluster", colormap=plot_color)
        p.remove_scalar_bar()
        p.camera_position = _cpos
        p.add_text(f" camera_position = '{_cpos}' ", position="upper_left",
                   font_size=font_size, color=font_color, font="arial")
        if i == 3:
            p.add_scalar_bar(title="cluster", title_font_size=font_size+10, label_font_size=font_size, color=font_color,
                             font_family="arial", vertical=True, fmt="%Id", n_labels=0, use_opacity=True)
    if save.endswith("png"):
        p.show(screenshot=save)
    else:
        path = p.generate_orbital_path(factor=2.0, shift=0, viewup=viewup, n_points=20)
        if save.endswith("gif"):
            p.open_gif(save)
        elif save.endswith("mp4"):
            p.open_movie(save, framerate=framerate, quality=5)
        p.orbit_on_path(path, write_frames=True, viewup=(0, 0, 1), step=0.1)
        p.close()


