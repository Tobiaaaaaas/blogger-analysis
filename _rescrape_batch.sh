#!/bin/bash
cd "d:/claude_code_ana/blogger-analysis"
declare -A BLOGGERS=(
["股傲"]="Ciee4gDCnhFeJziMVuS7RI_Rz6LdjNyc2CuvmTDrn0qzhYWuvnRSiXQaSQo8AAAAAAAAAAAAAFDH72SSKFr_nyQ54JeXeFR3KYNqL-d8amPXsvlVq7JELfFlDiRFXXDx959RERuFo5cKEKPJmQ4Yw8WD6gQiAQMFmMrc="
["股市求是"]="CifhdsIJhOsmYSwzzDZ5eU97lTNSShvv_BDysZfxSXOVDlaONcg5s-MaSQo8AAAAAAAAAAAAAFDH72SSKFr_nyQ54JeXeFR3KYNqL-d8amPXsvlVq7JELfFlDiRFXXDx959RERuFo5cKEO3ImQ4Yw8WD6gQiAQNHJTcJ="
["时间轨迹"]="CieoXLjiVcRVfpUvuXoEHCMWTF1ELANBhj5YzHv_fq12ZmonNJLh0-4aSQo8AAAAAAAAAAAAAFDH72SSKFr_nyQ54JeXeFR3KYNqL-d8amPXsvlVq7JELfFlDiRFXXDx959RERuFo5cKELDJmQ4Yw8WD6gQiAQMWC0QH="
["时间合伙人"]="Cidm2ckwAneaySBbF69JUBZpT16EeLyG8hIp8_89HQMBCeXusJhBUekaSQo8AAAAAAAAAAAAAFDH72SSKFr_nyQ54JeXeFR3KYNqL-d8amPXsvlVq7JELfFlDiRFXXDx959RERuFo5cKEKLJmQ4Yw8WD6gQiAQOyn_LD="
)
for name in "${!BLOGGERS[@]}"; do
  echo "========== 重爬 $name =========="
  PYTHONIOENCODING=utf-8 python scripts/pipeline/scrape_toutiao.py "https://www.toutiao.com/c/user/token/${BLOGGERS[$name]}/?source=m_redirect" --name "$name" 2>&1 | tail -8
done
echo "========== 全部完成 =========="
