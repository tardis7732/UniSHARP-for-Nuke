[한국어](README.md) | [English](README.en.md)

# UniSHARP for Nuke

**Turn one image into a 3D Gaussian Splat scene, then move the camera and render inside Nuke 17.**

This project connects [UniSHARP](https://github.com/Insta360-Research-Team/UniSHARP) single-image reconstruction to a Nuke node workflow. A Python controller runs inside Nuke, while a separate Python process handles AI inference. Nuke 17's built-in 3D nodes load and render the generated scene.

Current version: **1.1.4** · Windows installation scripts · Tested in **Nuke 17.0v3**

![Generated Gaussian scene in Nuke's 3D Viewer](docs/images/nuke-3d-view.png)

*A Gaussian scene reconstructed from one photograph, viewed in Nuke's 3D Viewer.*

## What it does

- Captures one frame from a connected image and reconstructs 3D Gaussians with UniSHARP.
- Creates an estimated source camera and an editable native Nuke 3D scene.
- Lets you move and rotate the camera and render through `SplatRender`.
- Shows processing stages, elapsed time, AI image dimensions, and the last result's statistics.
- Reuses the existing scene and preserves camera edits when showing the same result again.
- Creates a separate output folder and scene version for each new generation.

The workflow is **input image → UniSHARP controller → external Python inference → Gaussian PLY and camera metadata → Nuke 3D scene**. Blender, Unreal Engine, and a browser UI are not required for this Nuke workflow.

![Native nodes created by UniSHARP in Nuke](docs/images/nuke-node-graph.png)

*The generated graph uses `GeoReference`, `GeoColorSpace`, `Camera4`, `SplatRender`, and a transparent `Constant` that sets the output format. OCIO projects also receive a working-space conversion node.*

> **The controller's 2D output passes through the original input.** View the generated image at the separate `SplatRender` output, or at `UniSHARP_WorkingSpace` when that conversion node is present.

## Requirements

| Component | Requirement |
|---|---|
| Operating system | 64-bit Windows. Installation and removal scripts use PowerShell. |
| Nuke | Nuke 17. Actual generation and rendering were tested in **17.0v3**. |
| Python | External **64-bit Python 3.11**, separate from Nuke's Python |
| AI environment | Tested with **PyTorch 2.8.0 + CUDA 12.8** and torchvision 0.23.0 |
| GPU | A CUDA-capable NVIDIA GPU. Tested on an **RTX 3070 with 8 GB**. |
| Model | Official `pretained_model.pt`, approximately **4.73 GB**, downloaded separately |
| Storage | Space for the model, Python environment, input snapshots, PLY files, and renders |

Do not install PyTorch into Nuke's embedded Python. Model weights, virtual environments, and Nuke installers are not distributed in this repository. The default inference setting is **AI image edge 512**.

## Installation

### 1. Download the repository

Use GitHub's **Code → Download ZIP** and extract the archive, or run:

```powershell
git clone https://github.com/tardis7732/UniSHARP-for-Nuke.git
cd UniSHARP-for-Nuke
```

Run subsequent commands from this repository folder. Keep the folder in place after installation.

### 2. Create an external Python environment

With Python 3.11 installed, run these commands in order. Confirm each command succeeds before continuing.

```powershell
py -3.11 -m venv .venv-nuke
& ".\.venv-nuke\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv-nuke\Scripts\python.exe" -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
& ".\.venv-nuke\Scripts\python.exe" -m pip install -r .\nuke\requirements-nuke.txt
```

If `py` is unavailable, replace `py -3.11` in the first command with your Python 3.11 executable, for example: `& "C:\Python311\python.exe" -m venv .venv-nuke`.

### 3. Download the model

```powershell
& ".\.venv-nuke\Scripts\python.exe" .\nuke\download_checkpoint.py
```

The helper downloads `checkpoints/pretained_model.pt` from the official [Insta360-Research/Unisharp](https://huggingface.co/Insta360-Research/Unisharp) distribution and checks its SHA-256. The spelling `pretained_model.pt` matches the upstream file. The source, revision, and hash are recorded locally in `checkpoints/source.json`.

### 4. Register the plugin in Nuke

**Double-click `Install_Nuke17.bat`**, or run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install_Nuke17.ps1 -PythonExe ".\.venv-nuke\Scripts\python.exe"
```

The installer checks the runtime and model paths and adds the plugin path to your user's `.nuke/init.py`. It preserves existing content and backs up the file before changing it. Repeating the same installation does not duplicate the registration block.

Restart any Nuke instance that was already open, then create the controller from **Nodes → UniSHARP → UniSHARP (Nuke 17)**. Nuke instances launched after installation load it on startup.

### Reuse an existing environment and model

You can supply an external Python environment that already has the inference dependencies and a local model file. The installer does not automatically reinstall packages into an existing environment.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install_Nuke17.ps1 -PythonExe "D:\AI\UniSHARP\.venv\Scripts\python.exe" -Checkpoint "D:\Models\pretained_model.pt"
```

If you need a new environment but already have the model, you can also use `-CreateEnvironment -BasePythonExe "C:\Python311\python.exe" -Checkpoint "D:\Models\pretained_model.pt"`. This creates `.venv-nuke` in the repository and installs its dependencies. It does not download the model automatically; prepare the model download first when necessary.

Configuration is stored locally in `nuke/config.json`. To use a different Nuke configuration folder, add `-NukeUserDir "D:\NukeConfig\.nuke"` to the installation or removal command.

## Usage

1. Connect an image node, such as `Read`, to **input 0 of UniSHARP**.
2. Move the timeline to the desired frame. New controllers use the timeline frame at the moment you press Generate.
3. Select **AI image edge**. Start with **512**.
4. Press **Generate Gaussian** on the row below **Output folder**.
5. Follow the status display. The generated result appears in the Viewer when complete.
6. Adjust the generated `Camera4` to change the viewpoint, and connect a `Write` node to the generated image output to render.

The Viewer automatically receives **only the generated output**. The source image is not automatically connected as a B input.

![UniSHARP controller properties](docs/images/nuke-node-properties.png)

*The controller's properties. The `Result JSON` field remains visible and is filled automatically after a successful generation.*

### Input and main controls

| Input or control | Behavior |
|---|---|
| Input 0 | Source 2D image. Use a regular perspective image with square pixels, pixel aspect 1. |
| Use timeline frame when generating | On by default. Processes the timeline frame when Generate is pressed. |
| Source frame | Editable fixed frame number when timeline-following is off. |
| Use current frame | Copies the timeline frame into Source frame in fixed-frame mode. |
| AI image edge | Longest edge used for AI processing: **512 / 768 / 1024**. |
| Output folder | Output location. The installer defaults to `Nuke_Output` inside the repository. |
| Generate Gaussian | Generates a new result and a separate scene from one frame. |
| Cancel | Stops the running job. Saved inputs and logs remain on disk. |
| Build / Show Scene | Builds a scene from the selected Result JSON or reuses its existing scene. |
| Show Result | Shows the generated scene in the Viewer. |
| Show result in Viewer when complete | On by default. Connects the Viewer to the result after generation. |
| Open Output / Open Last Log | Opens the most recently attempted job's folder or log, including a failed job. |
| Status / Last result | Processing stage, elapsed time, and the last result's source/AI sizes, Gaussian count, and runtime. |
| Result JSON | Automatically populated result path. You can also select a saved `result.json` to load another result. |

The **Settings** tab contains the external Python executable, checkpoint path, and `cuda`/`cpu` selection. `Check Environment` checks dependencies, the device, and the model file without generating an image. A CPU option is provided; the published benchmarks and actual runtime validation cover **CUDA**.

### Keep scenes and output files together

![Generated Camera4 properties and Gaussian scene](docs/images/nuke-camera-properties.png)

*The generated `Camera4` contains the estimated camera values. Adjust translation and rotation in its `Position` tab to change the viewpoint, then inspect the result through `SplatRender`.*

Pressing **Build / Show Scene** or **Show Result** again for the same result reuses the existing scene tagged by this plugin. Camera edits survive node renaming and saving/reopening the `.nk` script. **A new Generate adds a separate scene**, preserving earlier results and edits.

Each output folder contains the source snapshot, `gaussians.ply`, `camera.json`, `result.json`, progress data, and `worker.log`. Keep this folder with the `.nk` script. The result JSON is needed to reconstruct the scene.

After moving to another location or PC, check the Python and model paths and the file references in the `.nk` script. Both the Result JSON field and the PLY path stored inside that JSON must point to the new location. Configure a Python environment appropriate for the destination machine and GPU.

### Reload or remove the plugin

After updating the code, use **Nodes → UniSHARP → Reload Plugin** when no job is running. If an already-open older version has no reload menu, restart Nuke once. Older controllers retain their fixed source frame and receive additional controls in a **Workflow** tab.

The removal command deletes only the plugin's startup registration block. Models, environments, and generated outputs remain on disk.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Uninstall_Nuke17.ps1
```

## Resolution and validation

**AI image edge and output resolution are separate settings.** Selecting 1024 for a 5346×3568 photograph processes a 1024×683 image with AI and renders at 5346×3568 in Nuke. The estimated camera is scaled to the original output dimensions.

![Generated image rendered through Nuke SplatRender](docs/images/nuke-render.png)

*The generated Gaussian scene rendered through Nuke's SplatRender.*

These measurements use the same **5346×3568** source photograph on **Windows, Nuke 17.0v3, RTX 3070 8 GB, Python 3.11, and PyTorch 2.8.0 + CUDA 12.8**.

| AI image edge | Processed image | Gaussians | Worker runtime | Peak PyTorch allocation |
|---|---|---:|---:|---:|
| 512 | 512×342 | 350,208 | 12.703 s | 3.77 GiB |
| 1024 | 1024×683 | 1,398,784 | 28.719 s | 7.62 GiB |

Worker runtime includes model preparation, inference, and output writing. Total waiting time also includes Nuke's input capture and final rendering. These are reference measurements for this photograph and machine.

Validation covered Nuke input capture, external CUDA inference, PLY loading, native rendering, camera movement, script save/reopen, and preservation of camera edits. Output conversion was also tested with default OCIO, ACES 1.3, and ACES 2.0 configurations.

Measurements and public-package checks are recorded in the [validation report](docs/validation.json).

## Limitations and troubleshooting

- Processes **one frame per run**. Temporally consistent sequences, 4D reconstruction, and real-time frame processing are not supported.
- A single photograph provides little evidence for hidden surfaces and occluded structures. Begin with small viewpoint changes near the estimated source camera.
- The input is saved as **8-bit sRGB PNG**, and alpha is ignored. This inference path does not preserve HDR ranges or every source channel.
- The workflow targets regular perspective images. It does not include fisheye or panorama controls. Add a `Reformat` before the controller if the source pixel aspect is not 1.
- A large output PNG does not imply recovery of all source-resolution detail. The 1024 run above used an internal feature extraction size of **602×406**. A 5K output is not native 5K model inference.
- The 1024 run left little memory headroom on the RTX 3070 8 GB. Keep 512 as the default and close other GPU workloads if memory is exhausted. PyTorch allocator peaks are separate from a measurement of the entire process's physically resident VRAM.
- On failure, check **Status** and **Open Last Log** first. Use `Check Environment` to check the Python path, checkpoint, and CUDA availability.
- A custom OCIO configuration must expose the color spaces needed for sRGB input conversion and Linear Rec.709 output conversion. Compatibility with every custom configuration is not guaranteed.
- Current validation covers Windows/Nuke 17.0v3. Other operating systems and Nuke versions require separate testing.

## Credits and license

This is an unofficial Nuke integration. Attribution and notices for the upstream research code and original local workflow are retained.

| Component | Source | License |
|---|---|---|
| UniSHARP and repository root | [Insta360 Research Team / UniSHARP](https://github.com/Insta360-Research-Team/UniSHARP) | [CC BY-NC 4.0](LICENSE) |
| UniK3D | [lpiccinelli-eth / UniK3D](https://github.com/lpiccinelli-eth/UniK3D) | [CC BY-NC-SA 4.0](UniK3D/LICENSE) |
| Local workflow used for the Nuke adaptation | [tardis7732 / UniSHARP-Blender-Workflow](https://github.com/tardis7732/UniSHARP-Blender-Workflow) | Retains the included upstream licenses and notices |

**Noncommercial conditions apply.** See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component notices. Separately downloaded models and installed packages carry their respective distribution terms.

Example photograph shown in the screenshots: **Jaroslaw Ceborski**, [Living room (Unsplash) on Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Living_room_(Unsplash).jpg), [original Unsplash photograph](https://unsplash.com/photos/jn7uVeCdf6U). The Commons file page identifies it as **CC0 1.0**. Screenshots show actual Nuke sessions; the original photograph and model are not included in the repository distribution.
