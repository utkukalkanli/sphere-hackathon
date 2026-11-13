# Text + image to CAD: practice set

Build a pipeline that turns a written design brief and 8 rendered views of a mechanical part into
a CAD solid. Your pipeline will be run on a hidden set of parts that you have never seen, and
scored with the same scorer that is included here.

## What you get per part

```
parts/<id>/
  images/view_0_front.png ... view_7_iso_back_left_bottom.png   8 orthographic views, 1024 px
  context.png        the part highlighted in orange inside its assembly (only for assembly parts)
  description.md     design brief: function, envelope, features with exact dimensions in mm
  facts.json         measurements the brief was written from (practice set only, for debugging)
```

Inputs to your pipeline: `images/`, `context.png` (if present) and `description.md`. Nothing
else is available for hidden parts.

All views are orthographic and rendered in the part's own X/Y/Z frame:

| file | camera |
|---|---|
| view_0_front | looking along +Y: X to the right, Z up |
| view_1_back | looking along -Y: X to the left, Z up |
| view_2_left | looking along +X: Y to the left, Z up |
| view_3_right | looking along -X: Y to the right, Z up |
| view_4_top | looking along -Z: X to the right, Y up |
| view_5_bottom | looking along +Z: X to the right, Y down |
| view_6_iso_front_right_top | isometric from +X, -Y, +Z |
| view_7_iso_back_left_bottom | isometric from -X, +Y, -Z |

Positions in `description.md` are in mm from the minimum corner of the part's bounding box.