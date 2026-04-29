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

# Producer FastAPI artifact (권장: simplified spec 사용)
openapi-generator + python-fastapi 조합에서 `oneOf`/`anyOf`/대형 integer bound 이슈를 피하기 위해,
원본 YAML은 유지하고 `simplified/`에 생성 전용 YAML 세트를 만들어 빌드합니다.

```bash
# simplified/*.yaml 재생성
python tools/build_simplified_specs.py

# producer 4종(nncof/nupf/nnef/nsmf) artifacts 일괄 재생성
python tools/build_producer_artifacts.py
```

`tools/build_producer_artifacts.py`는 반드시 `simplified/`를 기준으로 generator를 실행하여,
상대 `$ref`가 원본이 아니라 simplified 공통 YAML을 참조하도록 강제합니다.

# Callback Receiver (소비자 측 server stub)
OpenAPI Generator는 OpenAPI `callbacks:` 섹션에 대해 server stub을 생성하지 않으므로,
콜백을 받는 NF(예: Nncof의 경우 PCF/RICF)가 구현해야 할 endpoint를 별도 spec으로
`callbacks/` 디렉토리에 자동 생성합니다.

`callbacks/*.yaml`은 **generated artifact** 입니다 (직접 수정 금지).
원본 NF YAML이 변경되면 아래 명령으로 재생성하세요.

# Callback YAML 재생성 (PyYAML 필요: pip install pyyaml)
python tools/build_callbacks.py            # 전체 재생성
python tools/build_callbacks.py nncof      # 특정 NF만

# Nncof Callback Receiver (PCF/RICF가 구현)
java -jar /home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar generate \
  -i /home/labry/git/ncof_yaml/callbacks/Nncof_EventsSubscriptionNotification_Callback_PoC_ETRI_DoDo1.yaml \
  -g python-fastapi -o /home/labry/git/ncof_generated/nncof_cb --package-name nncof_cb

# Nupf Callback Receiver (NCOF가 구현)
java -jar /home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar generate \
  -i /home/labry/git/ncof_yaml/callbacks/Nupf_EventExposure_Notification_Callback_PoC_ETRI_DoDo1.yaml \
  -g python-fastapi -o /home/labry/git/ncof_generated/nupf_cb --package-name nupf_cb

# Nnef Callback Receiver (NCOF가 구현)
java -jar /home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar generate \
  -i /home/labry/git/ncof_yaml/callbacks/Nnef_EventExposure_Notification_Callback_PoC_ETRI_DoDo1.yaml \
  -g python-fastapi -o /home/labry/git/ncof_generated/nnef_cb --package-name nnef_cb

# Nsmf Callback Receiver (NCOF가 구현)
java -jar /home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar generate \
  -i /home/labry/git/ncof_yaml/callbacks/Nsmf_EventExposure_Notification_Callback_PoC_ETRI_DoDo1.yaml \
  -g python-fastapi -o /home/labry/git/ncof_generated/nsmf_cb --package-name nsmf_cb
