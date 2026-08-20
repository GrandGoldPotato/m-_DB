# m당단가 Ver.4 공식자료 자동감지 패키지

## 현재 자동화 범위
- 매일 09:00(KST) 공식 페이지 변경 여부를 자동 확인합니다.
- `source_status.json`에 페이지 해시, ETag, Last-Modified, 변경 여부를 기록합니다.
- B급 자료(품셈/표준시장단가/노임/제비율/행안부 예규)는 변경 감지 후 검증이 필요합니다.
- A급 자료(나라장터 가격정보 OpenAPI)는 수치 자동반영 후보입니다.

## 중요한 제한
이 패키지는 '공식자료가 바뀌었는지'를 자동 감지하는 1단계입니다.
조달청 OpenAPI의 실제 자재단가 자동반영은 공공데이터포털 서비스키와
상수도 자재 품목코드 매핑표가 있어야 안전하게 구현할 수 있습니다.

## GitHub 적용
1. 이 ZIP의 폴더 구조를 `mdang-unitprice-db` 저장소에 업로드합니다.
2. 저장소 Actions 탭에서 `Official source watch`를 1회 수동 실행합니다.
3. 이후 매일 자동 실행됩니다.
4. `source_status.json`에서 changed=true가 나오면 공식자료 변경을 검증합니다.

## 중앙 단가DB
Excel/Android는 동일한 `m당단가_DB.tsv`를 사용합니다.
Ver.4 초기 TSV의 `Ver4-FALLBACK`은 기존 가단가이며 공식 재산정 대기 상태입니다.
공식 산출이 끝난 행은 향후 버전을 `Ver4-OFFICIAL`로 변경하는 방식을 권장합니다.
