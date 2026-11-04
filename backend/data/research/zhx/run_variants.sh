#!/bin/bash
cd /root/zerohero/backend/data/research/zhx
for v in X1_be_after_T1 X2_time_stop_12 X3_t1_min_0p8 X4_be_and_time12; do
  nice -n 10 /root/zerohero/backend/venv/bin/python validate.py /root/zerohero/backend/data/research/zerohero_edge/variants/$v.pine $v > validate_$v.log 2>&1
  echo "$v done $(date -Is)"
done
echo VARIANTS-DONE
