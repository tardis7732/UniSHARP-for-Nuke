# 서드파티 고지 및 라이선스

이 저장소는 여러 외부 연구 코드와 도구를 함께 사용합니다. 아래 고지는 원본 저작권·라이선스를 대체하지 않으며, 사용자는 각 원본의 최신 라이선스와 배포 조건을 확인해야 합니다.

## 포함된 소스

| 구성요소 | 출처 | 적용 라이선스 / 고지 |
| --- | --- | --- |
| UniSHARP | [Insta360-Research-Team/UniSHARP](https://github.com/Insta360-Research-Team/UniSHARP) | 저장소 루트의 [LICENSE](LICENSE)에 포함된 Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0). 원 저작권 표기: © 2026 Insta360 Research Team. |
| UniK3D | [lpiccinelli-eth/UniK3D](https://github.com/lpiccinelli-eth/UniK3D) | [`UniK3D/LICENSE`](UniK3D/LICENSE)에 포함된 Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0). `UniK3D/` 폴더의 원 저작권·라이선스·고지를 유지해야 합니다. |

## 링크로만 참조하는 외부 구성요소

아래 항목은 이 저장소에 소스를 재배포하지 않습니다. 설치·사용 시 해당 프로젝트의 최신 라이선스와 요구 사항이 적용됩니다.

| 구성요소 | 사용 위치 | 출처 |
| --- | --- | --- |
| 3DGEER | UniSHARP의 어안 렌더링 경로 | [boschresearch/3dgeer](https://github.com/boschresearch/3dgeer) |
| MLSLabs Gaussian Splatting Renderer | Unreal Engine에서 `*_unreal_gaussian_splat.ply`를 임포트/렌더링 | [mlslabs/MLSLabsGaussianSplattingRenderer-UE](https://github.com/mlslabs/MLSLabsGaussianSplattingRenderer-UE) |
| Blender | `.blend` 장면 생성 | [Blender](https://www.blender.org/) |
| PyTorch, gsplat, Gradio, Viser 및 Python 패키지 | 추론, 렌더링, GUI, PLY 미리보기 | 각 패키지의 배포본·저장소 라이선스 |

## 로컬 워크플로우 추가분

이 저장소의 `scripts/blender_gui.py`, Blender/PLY export 스크립트와 `docs/` 문서는 UniSHARP 기반 로컬 워크플로우를 위한 추가분입니다. 이 추가분을 포함해 재배포할 때에도 UniSHARP와 UniK3D에 적용되는 모든 저작자 표시, 비상업 및 동일조건변경허락 조건을 준수해야 합니다.

## 모델 및 생성물

학습된 체크포인트, 사용자 입력 이미지, 생성된 `.blend`/`.ply` 파일은 저장소에서 제외합니다. 모델 가중치와 데이터셋은 별도 배포처의 라이선스·이용 조건을 확인한 뒤 내려받고 사용하세요.
