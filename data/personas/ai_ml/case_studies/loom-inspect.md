---
project: loom-inspect
title: Fabric defect detection on the production line
client_industry: textile manufacturing
date: 2023-04-15
skills: [Computer Vision, YOLO, PyTorch, Object Detection]
synthetic: true
---

# Fabric defect detection on the production line

**Client:** Crescent Textile Co. (textile manufacturing, synthetic)

## Situation
A denim mill caught weaving defects only at final inspection.

## What I did
I collected and labelled 6,400 frames, trained a lightweight detector and exported it for an edge PC beside the loom.

## Result
92% mAP@0.5 and defects flagged within two seconds.

## Stack
Computer Vision, YOLO, PyTorch, Object Detection.

Code: see the `loom-inspect` repository.
