hello

# Nncof (메인)
java -jar /home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar generate \
  -i /home/labry/git/ncof_yaml/Nncof_EventsSubscription_PoC_ETRI_DoDo1.yaml \
  -g python-fastapi -o /home/labry/git/ncof_generated/nncof --package-name nncof

# Nsmf
java -jar /home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar generate \
  -i /home/labry/git/ncof_yaml/TS29508_Nsmf_EventExposure_PoC_ETRI_DoDo1.yaml \
  -g python-fastapi -o /home/labry/git/ncof_generated/nsmf --package-name nsmf

# Nupf
java -jar /home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar generate \
  -i /home/labry/git/ncof_yaml/TS29564_Nupf_EventExposure_PoC_ETRI_DoDo1.yaml \
  -g python-fastapi -o /home/labry/git/ncof_generated/nupf --package-name nupf

# Nnef
java -jar /home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar generate \
  -i /home/labry/git/ncof_yaml/TS29591_Nnef_EventExposure_PoC_ETRI_DoDo1.yaml \
  -g python-fastapi -o /home/labry/git/ncof_generated/nnef --package-name nnef

Domain YAML들(TS29571_CommonData, SupplementaryData 등)은 $ref로 자동 참조되므로 별도 실행 불필요합니다.  