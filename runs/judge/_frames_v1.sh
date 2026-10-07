#!/bin/bash
# usage: _frames_v1.sh VID NN_wXX "t1 t2 ..."  (clip-relative seconds, from the v1_test preview)
cd /d/shotvi
V=$1; P=runs/previews/v1_test/$V/$2.mp4; C=${2#*_}; D=runs/judge/$V/frames_v1/$C
rm -rf "$D"; mkdir -p "$D"; i=0; : > "$D/index.txt"
for t in $3; do i=$((i+1)); n=$(printf %02d $i)
  ffmpeg -v error -y -ss $t -i "$P" -frames:v 1 -vf "scale=400:-1,drawtext=text='$t':x=4:y=4:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6" "$D/$n.png" 2>/dev/null || ffmpeg -v error -y -ss $t -i "$P" -frames:v 1 -vf "scale=400:-1" "$D/$n.png"
  echo "$n -> t=$t" >> "$D/index.txt"
done
ffmpeg -v error -y -i "$D/%02d.png" -vf "tile=4x$(( (i+3)/4 )):padding=4:margin=4:color=white" "$D/sheet.png"
cat "$D/index.txt" | tr '\n' ' '; echo
