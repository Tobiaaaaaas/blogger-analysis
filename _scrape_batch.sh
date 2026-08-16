#!/bin/bash
cd "d:/claude_code_ana/blogger-analysis"
declare -A BLOGGERS=(
["小工匠说股市v"]="CixSFgt_ttaZ_ipGfouAi2Id3GwiUXSDhXg4Mj1jG9nVG4SD7Y9tz1wf0RlZZBpJCjwAAAAAAAAAAAAAUMiUnEEiRogMqOMqvgVZYJPFR0PeaGC9vodV5SL4o-VS-H5qLNb8vg2SIjH6gb3tFKsQucqZDhjDxYPqBCIBA_8Lelg="
["强哥解盘"]="CizXu5vRsXoYdk-ga4fwNJRiI6XSBVairUBdqcCe1CTQreu-e2EMyOSzRzewEBpJCjwAAAAAAAAAAAAAUMiUnEEiRogMqOMqvgVZYJPFR0PeaGC9vodV5SL4o-VS-H5qLNb8vg2SIjH6gb3tFKsQuMqZDhjDxYPqBCIBA3ufIwU="
["股傲"]="Ciee4gDCnhFeJziMVuS7RI_Rz6LdjNyc2CuvmTDrn0qzhYWuvnRSiXQaSQo8AAAAAAAAAAAAAFDH72SSKFr_nyQ54JeXeFR3KYNqL-d8amPXsvlVq7JELfFlDiRFXXDx959RERuFo5cKEKPJmQ4Yw8WD6gQiAQMFmMrc="
["股市求是"]="CifhdsIJhOsmYSwzzDZ5eU97lTNSShvv_BDysZfxSXOVDlaONcg5s-MaSQo8AAAAAAAAAAAAAFDH72SSKFr_nyQ54JeXeFR3KYNqL-d8amPXsvlVq7JELfFlDiRFXXDx959RERuFo5cKEO3ImQ4Yw8WD6gQiAQNHJTcJ="
["股评老陈"]="CiYqK7oW_ZJdjmYgX5XaktwbPuaoOvkB1m5hb0P-DEn2mePrD2FDgBpJCjwAAAAAAAAAAAAAUMfvZJIoWv-fJDngl5d4VHcpg2ov53xqY9ey-VWrskQt8WUOJEVdcPH3n1ERG4WjlwoQ_MiZDhjDxYPqBCIBAxEXBlw="
["枫叶"]="CiyUm46FnDn5kNWfOYDrpyd18dsocgV_emSav1VUl3tyjehiso63XFjkRrYeSBpJCjwAAAAAAAAAAAAAUMfvZJIoWv-fJDngl5d4VHcpg2ov53xqY9ey-VWrskQt8WUOJEVdcPH3n1ERG4WjlwoQo8mZDhjDxYPqBCIBAz_KBF0="
["时间轨迹"]="CieoXLjiVcRVfpUvuXoEHCMWTF1ELANBhj5YzHv_fq12ZmonNJLh0-4aSQo8AAAAAAAAAAAAAFDH72SSKFr_nyQ54JeXeFR3KYNqL-d8amPXsvlVq7JELfFlDiRFXXDx959RERuFo5cKELDJmQ4Yw8WD6gQiAQMWC0QH="
["时间合伙人"]="Cidm2ckwAneaySBbF69JUBZpT16EeLyG8hIp8_89HQMBCeXusJhBUekaSQo8AAAAAAAAAAAAAFDH72SSKFr_nyQ54JeXeFR3KYNqL-d8amPXsvlVq7JELfFlDiRFXXDx959RERuFo5cKEKLJmQ4Yw8WD6gQiAQOyn_LD="
["白猫财眼"]="Cig5faD7Ov0rsgGZewSCl4_DF9Xwgj6Yd2gryvwHhly4iSLFmUfkN44UGkkKPAAAAAAAAAAAAABQx-9kkiha_58kOeCXl3hUdymDai_nfGpj17L5VauyRC3xZQ4kRV1w8fefUREbhaOXChCjyZkOGMPFg-oEIgEDqLaXdg=="
)
for name in "${!BLOGGERS[@]}"; do
  echo "========== 爬取 $name =========="
  PYTHONIOENCODING=utf-8 python scripts/pipeline/scrape_toutiao.py "https://www.toutiao.com/c/user/token/${BLOGGERS[$name]}/?source=m_redirect" --name "$name" 2>&1 | tail -8
done
echo "========== 全部完成 =========="
