# AGR Collision

Blender 5.2 extension for generating closed, convex, non-overlapping Unreal
Engine UCX collision sets from selected architectural meshes.

Author and maintainer: **XIVgate**  
License: **GPL-3.0-or-later**

## Current workflow

1. Select one or more source mesh objects. The active object supplies the UCX
   base name.
2. Open **3D Viewport > Sidebar > AGR > AGR Collision**.
3. Use **Analyze Selected** to inspect the exact hidden working geometry.
4. Use **Generate / Regenerate** to atomically replace the previous generated
   collision set.
5. Use **Validate Colliders** before export.

Generated objects use the form `UCX_<SourceName>_001` and are placed in an
`AGR_COLLISION__<SourceName>` collection.

## Geometry pipeline

- combines evaluated selected meshes in world space without modifying sources;
- fuses nearby vertices before any boundary repair;
- preserves source coordinates, planes and architectural corners;
- recalculates closed shell normals outward;
- ignores connected narrow recesses below `Tolerance`, while retaining external
  steps and broad facade corners;
- detects repeated closed sweep/ring topology and decomposes it into logical
  neighbouring sectors before the generic search;
- splits concave solids along planes derived from their own reflex edges;
- connects each intermediate region into one convex hull whenever that hull is
  proven to remain inside the configured tolerance;
- separates disconnected islands after every cut, preserving large openings;
- keeps the largest solid during overlap refinement and applies the minimum
  `0.0002 m` support-plane gap;
- optionally removes separate thin details;
- validates closure, convexity, transforms, naming, intersections and the AGR
  triangle budget.

The pipeline does not use voxel remeshing.

## Localization

The extension includes English source text and a complete Russian translation.
Interface labels and tooltips follow Blender's independent translation controls
under **Edit > Preferences > Interface > Translation**.

## Default AGR settings

- tolerance: `0.10 m`;
- minimum feature: `0.10 m`;
- collider gap: `0.0002 m`;
- fuse distance: `0.02 m`;
- optional separate thin-part threshold: `0.05 m`.

## Installation

Install the packaged ZIP through **Edit > Preferences > Get Extensions >
Install from Disk**, then enable **AGR Collision**.

## License

GPL-3.0-or-later.
