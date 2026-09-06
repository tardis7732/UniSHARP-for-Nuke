"""UniK3D utility exports retained for the UniSHARP-for-Nuke inference subset.

Modified from the vendored UniK3D utilities: training, evaluation, KNN and
visualization entry points are omitted. The full research project is upstream.
See ../../../THIRD_PARTY_NOTICES.md and ../../LICENSE for attribution/licensing.
"""
from .camera import invert_pinhole, project_pinhole, unproject_pinhole
from .distributed import (barrier, get_dist_info, get_rank, get_world_size,
                          is_main_process, setup_multi_processes, setup_slurm,
                          sync_tensor_across_gpus)
from .geometric import spherical_zbuffer_to_euclidean, unproject_points
from .misc import (format_seconds, get_params, identity, recursive_index,
                   remove_padding, to_cpu)

__all__ = [
    "format_seconds", "remove_padding", "get_params", "identity",
    "is_main_process", "setup_multi_processes", "setup_slurm",
    "sync_tensor_across_gpus", "barrier", "get_world_size", "get_rank",
    "unproject_points", "spherical_zbuffer_to_euclidean", "get_dist_info",
    "to_cpu", "recursive_index", "invert_pinhole", "unproject_pinhole",
    "project_pinhole",
]
