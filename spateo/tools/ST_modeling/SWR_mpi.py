import argparse
import random

import numpy as np
from mpi4py import MPI
from MuSIC import MuSIC, VMuSIC

# For testing:
from spateo.plotting.static.space import plot_cell_signaling
from spateo.tools.ST_modeling.MuSIC_downstream import MuSIC_Interpreter
from spateo.tools.ST_modeling.MuSIC_upstream import MuSIC_target_selector

np.random.seed(888)
random.seed(888)

if __name__ == "__main__":
    # From the command line, run spatial GWR

    # Initialize MPI
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    parser = argparse.ArgumentParser(description="Spatial GWR")

    # Temporary- for selection of targets/predictors:
    parser.add_argument("-run_upstream", action="store_true")

    parser.add_argument("-adata_path", type=str)
    parser.add_argument(
        "-csv_path",
        type=str,
        help="Can be used to provide a .csv file, containing gene expression data or any other kind of data. "
        "Assumes the first three columns contain x- and y-coordinates and then dependent variable "
        "values, in that order.",
    )
    parser.add_argument(
        "-subsample",
        action="store_true",
        help="Recommended for large datasets (>5000 samples), otherwise model fitting is quite slow.",
    )
    parser.add_argument(
        "-multiscale",
        action="store_true",
        help="Currently, it is recommended to only create " "multiscale models for Gaussian regression models.",
    )
    # Flag to return additional metrics along with the coefficients for multiscale models.
    parser.add_argument("-multiscale_params_only", action="store_true")
    parser.add_argument("-mod_type", type=str, default="niche")
    parser.add_argument("-cci_dir", type=str)
    parser.add_argument("-species", type=str, default="human")
    parser.add_argument(
        "-output_path",
        default="./output/stgwr_results.csv",
        type=str,
        help="Path to output file. Make sure the parent directory is empty- "
        "any existing files will be deleted. It is recommended to create "
        "a new folder to serve as the output directory. This should be "
        "supplied of the form '/path/to/file.csv', where file.csv will "
        "store coefficients. The name of the target will be appended at runtime.",
    )
    parser.add_argument("-custom_lig_path", type=str)
    parser.add_argument(
        "-ligand",
        nargs="+",
        type=str,
        help="Alternative to the custom ligand path, can be used to provide a custom list of ligands.",
    )
    parser.add_argument(
        "-fit_ligands_grn",
        action="store_true",
        help="Set True to indicate that ligands should be "
        "included in the GRN model. If True and path to"
        "custom ligands list is not given, will"
        "automatically find ligands from the data. If False,"
        "will not include ligands in the GRN model.",
    )
    parser.add_argument("-custom_rec_path", type=str)
    parser.add_argument(
        "-receptor",
        nargs="+",
        type=str,
        help="Alternative to the custom receptor path, can be used to provide a custom list of receptors.",
    )
    parser.add_argument(
        "-fit_receptors_grn",
        action="store_true",
        help="Set True to indicate that receptors should be "
        "included in the GRN model. If True and path to"
        "custom receptors list is not given, will"
        "automatically find receptors from the data. If False, "
        "will not include receptors in the GRN model.",
    )
    parser.add_argument(
        "-custom_regulators_path",
        type=str,
        help="Only used for GRN models. This file contains a list of TFs (or other regulatory molecules)"
        "to constitute the independent variable block.",
    )
    parser.add_argument(
        "-tf",
        nargs="+",
        type=str,
        help="Alternative to the custom receptor path, can be used to provide a custom list of transcription factors "
        "or other regulatory molecules.",
    )
    parser.add_argument("-custom_pathways_path", type=str)
    parser.add_argument(
        "-pathway",
        nargs="+",
        type=str,
        help="Alternative to the custom pathway path, can be used to provide a custom list of pathways.",
    )
    parser.add_argument("-targets_path", type=str)
    parser.add_argument(
        "-target",
        nargs="+",
        type=str,
        help="Alternative to the custom target path, can be used to provide a custom list of target molecules.",
    )
    parser.add_argument("-init_betas_path", type=str)

    parser.add_argument("-normalize", action="store_true")
    parser.add_argument("-smooth", action="store_true")
    parser.add_argument("-log_transform", action="store_true")
    parser.add_argument(
        "-target_expr_threshold",
        default=0.05,
        type=float,
        help="For automated selection, the threshold proportion of cells for which transcript "
        "needs to be expressed in to be selected as a target of interest. Not used if 'targets_path' is not None.",
    )
    parser.add_argument(
        "-multicollinear_threshold",
        type=float,
        help="Used only if `mod_type` is 'slice'. If this argument is provided, independent variables that are highly "
        "correlated will be filtered out based on variance inflation factor threshold. A value of 5 or 10 is "
        "recommended. This can be useful in reducing computation time.",
    )

    parser.add_argument("-coords_key", default="spatial", type=str)
    parser.add_argument(
        "-group_key",
        default="cell_type",
        type=str,
        help="Key to entry in .obs containing cell type "
        "or other category labels. Required if "
        "'mod_type' is 'niche' or 'slice'.",
    )
    parser.add_argument(
        "-group_subset",
        nargs="+",
        type=str,
        help="If provided, only cells with labels that correspond to these group(s) will be used as prediction "
        "targets. Will search in key corresponding to the input to arg 'cell_type' if given.",
    )
    parser.add_argument(
        "-covariate_keys",
        nargs="+",
        type=str,
        help="Any number of keys to entry in .obs or .var_names of an "
        "AnnData object. Values here will be added to"
        "the model as covariates.",
    )

    parser.add_argument("-bw")
    parser.add_argument("-minbw")
    parser.add_argument("-maxbw")
    parser.add_argument(
        "-bw_fixed",
        action="store_true",
        help="If this argument is provided, the bandwidth will be "
        "interpreted as a distance during kernel operations. If not, it will be interpreted "
        "as the number of nearest neighbors.",
    )
    parser.add_argument(
        "-exclude_self",
        action="store_true",
        help="When computing spatial weights, do not count the "
        "cell itself as a neighbor. Recommended to set to "
        "True for the CCI models because the independent "
        "variable array is also spatially-dependent.",
    )
    parser.add_argument("-kernel", default="bisquare", type=str)
    parser.add_argument(
        "-n_neighbors",
        default=10,
        type=int,
        help="Only used if `mod_type` is 'niche', to define the number of neighbors "
        "to consider for each cell when defining the independent variable array.",
    )

    parser.add_argument("-distr", default="gaussian", type=str)
    parser.add_argument("-fit_intercept", action="store_true")
    parser.add_argument("-include_offset", action="store_true")
    parser.add_argument(
        "-no_hurdle",
        action="store_true",
        help="If True, do not implement spatially-weighted hurdle model- will only perform generalized linear "
        "modeling.",
    )
    parser.add_argument("-tolerance", default=1e-3, type=float)
    parser.add_argument("-max_iter", default=500, type=int)
    parser.add_argument("-patience", default=5, type=int)
    parser.add_argument("-ridge_lambda", type=float)

    parser.add_argument(
        "-chunks",
        default=1,
        type=int,
        help="For use if `multiscale` is True- increase the number of parallel processes. Can be used to help prevent"
        "memory from running out, otherwise keep as low as possible.",
    )

    # Options for downstream analysis:
    parser.add_argument(
        "-search_bw",
        help="Used for downstream analyses; specifies the bandwidth to search for "
        "senders/receivers. Recommended to set equal to the bandwidth of a fitted "
        "model.",
    )
    parser.add_argument(
        "-top_k_receivers",
        default=10,
        type=int,
        help="Used for downstream analyses, specifically :func `infer_effect_direction`; specifies the number of top "
        "senders/receivers to consider for each cell.",
    )
    parser.add_argument(
        "-filter_targets",
        action="store_true",
        help="Used for downstream analyses, specifically :func `infer_effect_direction`; if True, will subset to only "
        "the targets that were predicted well by the model.",
    )
    parser.add_argument(
        "-filter_targets_threshold",
        default=0.65,
        type=float,
        help="Used for downstream analyses, specifically :func `infer_effect_direction`; specifies the threshold "
        "Pearson coefficient for target subsetting. Only used if `filter_targets` is True.",
    )
    parser.add_argument(
        "-diff_sending_or_receiving",
        default="sending",
        type=str,
        help="Used for downstream analyses, specifically :func `sender_receiver_effect_deg_detection`; specifies "
        "whether to compute differential expression of genes in cells with high or low sending effect potential "
        "('sending cells') or high or low receiving effect potential ('receiving cells').",
    )

    t1 = MPI.Wtime()

    # # Testing time! Uncomment this (and comment anything below) to test the downstream functions:
    # test_downstream = MuSIC_Interpreter(comm, parser)
    # # test_downstream.compute_coeff_significance()
    # # test_downstream.get_sig_potential()
    # test_downstream.compute_cell_type_coupling()

    # # Testing time! Uncomment this (and then comment anything above and below) to test the upstream functions:
    # # test = MuSIC_target_selector(parser)
    # test_gene = self.adata[:, "MMP1"].X
    # # test.parse_predictors(data=test_gene)
    #
    # Check if GRN model is specified:
    # if parser.parse_args().grn:
    #     "filler"

    # else:
    # For use only with VMuSIC:
    n_multiscale_chunks = parser.parse_args().chunks

    if parser.parse_args().run_upstream:
        swr_selector = MuSIC_target_selector(parser)
        swr_selector.select_features()

    if parser.parse_args().multiscale:
        print(
            "Multiscale algorithm may be computationally intensive for large number of features- if this is the "
            "case, it is advisable to reduce the number of parameters."
        )
        multiscale_model = VMuSIC(comm, parser)
        multiscale_model.multiscale_backfitting()
        multiscale_model.multiscale_compute_metrics(n_chunks=int(n_multiscale_chunks))
        multiscale_model.predict_and_save()

    else:
        swr_model = MuSIC(comm, parser)
        swr_model._set_up_model()
        swr_model.fit()
        swr_model.predict_and_save()

    t_last = MPI.Wtime()

    wt = comm.gather(t_last - t1, root=0)
    if rank == 0:
        print("Total Time Elapsed:", np.round(max(wt), 2), "seconds")
        print("-" * 60)
