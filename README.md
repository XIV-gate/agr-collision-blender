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

Version 1.2.4 assigns every generated UCX object the exact origin of its base
render object. AGR Prepare passes the shared source-derived Main/Glass pivot;
standalone generation inherits the active source object's origin. Hull world
geometry is counter-translated and therefore does not move.

Version 1.2.6 discards any sub-clearance fragment that collapses to a plane
after inset, preventing invalid zero-volume UCX leftovers. Version 1.2.5
reconstructs the final collider clearance from shifted support
planes. Clearance is now a constant world-space distance on every face; tall
or otherwise high-aspect-ratio hulls no longer lose centimetres at their ends
because of uniform scaling around the hull centre.

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

Version 1.0.1 keeps English as the source UI and documentation language and
provides native Russian labels and descriptions for every operator.

Version 1.1.0 keeps only source selection, quality, generation and validation
in the primary panel. Geometry preprocessing, convex-search limits and viewport
display behavior are grouped under a collapsed advanced panel.

Version 1.2.0 adds a selection-independent integration API for AGR Prepare.
Prepare can pass an explicit collision proxy, request a UCX set named from the
prepared High Main, and place the validated hulls directly in that High
package without changing viewport selection or modifying the proxy.

Version 1.2.1 guarantees transactional scene cleanup when final collider
validation raises or fails. AGR Collision remains scene-only: it creates no
cache, report or output folders; Prepare and Output own all filesystem paths.

Version 1.2.2 makes lossless generation the explicit default: no source
component is removed and no broad source fusion is allowed unless the user
enables destructive advanced preprocessing. Source vertices and face centres
must remain covered by the generated hull set. Overlapping sources can share a
validated covering result, but nearby sources with a real air gap remain
separate; the regression suite protects a 10 mm gap and a detached 0.2 m
detail.

Version 1.2.3 makes the safety boundary explicit in the main panel.  The
effective quality controls stay visible, while topology-changing `Min Feature`
and collider removal live only in the collapsed advanced panel.  The main
action cannot accidentally delete the current UCX set: regeneration still
builds and validates a candidate before atomically replacing it.

Current version: 1.2.5.

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
