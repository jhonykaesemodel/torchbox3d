[contributing-image]: https://img.shields.io/badge/contributions-welcome-brightgreen.svg?style=flat

<h1> <p align="center"> TorchBox3D </p> </h1>

> `torchbox3d` is a *3D perception* library for **autonomous driving datasets**.

## Supported Models

- Architectures:
  - [SECOND [Sensors 2018]](https://pdfs.semanticscholar.org/5125/a16039cabc6320c908a4764f32596e018ad3.pdf)

- Heads:
  - [CenterPoint [CVPR 2021]](https://openaccess.thecvf.com/content/CVPR2021/papers/Yin_Center-Based_3D_Object_Detection_and_Tracking_CVPR_2021_paper.pdf)

## Supported Datasets

- [Argoverse 2 Sensor Dataset [NeurIPS Datasets and Benchmarks]](https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/hash/4734ba6f3de83d861c3176a6273cac6d-Abstract-round2.html)

## Installation
---

### Source Install

This will install `torchbox3d` as a `conda` package.

```bash
bash conda/install.sh
```

## Configuration
---

### Configuring a training run

The project configuration file can be found in `conf/config.yaml`.

### Launching training

To launch a training session, simply run:

```bash
conda activate torchbox3d
python scripts/train.py
```

### Monitoring a training run

```bash
tensorboard --logdir experiments
```

### Citing this repository

```BibTeX
@software{Wilson_torchbox3d_2022,
  author = {Wilson, Benjamin and Pontes, Jhony},
  month = {4},
  title = {{torchbox3d}},
  url = {https://github.com/benjaminrwilson/torchbox3d},
  version = {0.0.1},
  year = {2022}
}
```