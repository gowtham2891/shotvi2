#!/bin/bash
# usage: _frames_r2.sh NN_wXX "t1 t2 t3 ..."  (clip-relative seconds, from the r2 preview)
P=runs/previews/gold_hm_r2/epZ0lbXkgWA/$1.mp4; C=${1#*_}; D=runs/judge/epZ0lbXkgWA/frames_r2/$C
rm -rf "$D"; mkdir -p "$D"; i=0; : > "$D/index.txt"
for t in $2; do i=$((i+1)); n=$(printf %02d $i)
  ffmpeg -v error -y -ss $t -i "$P" -frames:v 1 -vf "scale=480:-1,drawbox=0:0:120:22:black@0.7:t=fill" "$D/$n.png"
  echo "$n -> t=$t" >> "$D/index.txt"
done
ffmpeg -v error -y -i "$D/%02d.png" -vf "tile=4x$(( (i+3)/4 )):padding=4:margin=4:color=white" "$D/sheet.png"
cat "$D/index.txt" | tr '\n' ' '; echo
