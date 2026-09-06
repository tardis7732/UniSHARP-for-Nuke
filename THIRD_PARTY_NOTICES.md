# Third-party notices and source provenance

This is a Nuke integration and an inference subset of existing research code.
Original copyright headers and component licences remain in effect. The Nuke
integration does not replace or broaden those licences.

## Source lineage

The source snapshot was adapted from
[`tardis7732/UniSHARP-Blender-Workflow`, commit `9a1cb58`](https://github.com/tardis7732/UniSHARP-Blender-Workflow/commit/9a1cb58).
That workflow vendors UniSHARP and UniK3D research sources. The original snapshot's
third-party notice is preserved verbatim in [UPSTREAM_NOTICES.md](UPSTREAM_NOTICES.md)
as historical provenance; its Blender/Unreal/browser descriptions refer to that
upstream workflow, not to components shipped here.

| Included component | Source and attribution | Preserved licence |
| --- | --- | --- |
| UniSHARP inference model and utilities | [Insta360-Research-Team/UniSHARP](https://github.com/Insta360-Research-Team/UniSHARP); Copyright (c) 2026 Insta360 Research Team | [LICENSE](LICENSE), Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0) |
| UniK3D models, layers, camera utilities and evaluation configuration | [lpiccinelli-eth/UniK3D](https://github.com/lpiccinelli-eth/UniK3D); Luigi Piccinelli and the UniK3D authors; attribution headers retained | [UniK3D/LICENSE](UniK3D/LICENSE), Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) |
| DINOv2-derived layers within `UniK3D/unik3d/models/metadinov2/` | [facebookresearch/dinov2](https://github.com/facebookresearch/dinov2); Copyright (c) Meta Platforms, Inc. and affiliates; original headers retained | [third_party_licenses/DINOv2_LICENSE](third_party_licenses/DINOv2_LICENSE), Apache License 2.0, copied from the official DINOv2 source on 2026-09-07 |

The included `UniK3D/LICENSE` and per-file CC BY-NC-SA notices are retained as
supplied by the source snapshot. They take precedence over any less specific
description in a research README. The separately identified Meta layer licence
is preserved alongside the enclosing project's licence.

## Nuke adaptation

The added Nuke controller, standalone worker, installers and tests implement
single-image perspective inference, a vertex-only Gaussian PLY export in Nuke
coordinates, camera metadata, native Nuke scene construction, progress reporting
and an external Python runtime. Modifications to upstream inference sources add
local full-checkpoint model construction without redundant pretrained downloads
and omit training/evaluation imports from the packaged utility entry point.
These changes are distributed with the applicable upstream component terms.

Changed upstream files are `unisharp/models/unisharp_feature.py`
(optional local checkpoint construction), `unisharp/utils/unik3d_adapter.py`
(local model configuration without pretrained downloads), and
`UniK3D/unik3d/utils/__init__.py` (inference-only exports). The Nuke integration
files under `nuke/`, the installers and the accompanying tests are additions.

The source package intentionally omits training entry points, datasets, KNN
extension sources, browser interfaces, Blender/Unreal export tools, model weights,
Python environments and generated scene caches. It is not the complete upstream
research distribution.

## Separately installed software and model weights

Nuke and its native Gaussian rendering nodes are supplied and licensed by
[Foundry](https://www.foundry.com/products/nuke); no Nuke executable or plugin
binary is redistributed. PyTorch, torchvision and the Python dependencies listed
in [nuke/requirements-nuke.txt](nuke/requirements-nuke.txt) retain their own
licences and are installed separately.

The downloader obtains the checkpoint from
[Insta360-Research/Unisharp on Hugging Face](https://huggingface.co/Insta360-Research/Unisharp),
records its repository revision and verifies the advertised SHA-256 when present.
Checkpoint and input-data terms apply separately. No trained weights, user input
images or generated PLY files are included in this source package.

## UniK3D citation

Luigi Piccinelli, Christos Sakaridis, Mattia Segu, Yung-Hsu Yang, Siyuan Li,
Wim Abbeloos and Luc Van Gool. *UniK3D: Universal Camera Monocular 3D Estimation*.
IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR), 2025.
