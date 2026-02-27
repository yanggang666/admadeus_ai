# amadeus_ai

Amadeus Self-Service API를 Claude 코워크(MCP)와 Excel에 연결하기 위한 Python MCP 서버입니다.

주요 목표:
- 개인 사용 기준 최소 비용 운용
- 쿼터 초과 방지
- Excel에 바로 붙여넣기 쉬운 형태로 데이터 제공

## 1. 설치

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## 2. 환경 변수 설정

`.env.example`을 `.env`로 복사한 뒤 값 입력:

```env
AMADEUS_CLIENT_ID=your_client_id
AMADEUS_CLIENT_SECRET=your_client_secret
AMADEUS_HOST=https://api.amadeus.com
AMADEUS_TIMEOUT_SECONDS=20

CACHE_TTL_SECONDS=20
PRICE_CACHE_TTL_SECONDS=15

MAX_REQUESTS_PER_MINUTE=12
MAX_REQUESTS_PER_DAY=250
ALLOW_STALE_CACHE_ON_LIMIT=true
QUOTA_STATE_FILE=.quota_state.json
```

설명:
- 테스트 환경: `AMADEUS_HOST=https://test.api.amadeus.com`
- 실사용(실시간): `AMADEUS_HOST=https://api.amadeus.com`
- `CACHE_TTL_SECONDS`/`PRICE_CACHE_TTL_SECONDS`를 짧게 두면 최신성은 올라가고 호출 수는 늘어납니다.

## 3. 코워크 env vs .env 우선순위

현재 코드(`load_dotenv`) 기준으로 적용 순서는 아래와 같습니다.

1. 코워크 MCP 설정의 `env`
2. 프로젝트 루트의 `.env`

즉, 코워크에 `AMADEUS_CLIENT_ID/SECRET`를 넣으면 그 값이 우선 사용됩니다.

권장:
- 개인 단독 사용: `.env`만 관리
- 코워크/팀 사용: 사용자별로 코워크 `env`에서 각자 키 관리

## 4. 코워크가 README를 자동으로 읽는가?

- README는 기본적으로 문서입니다.
- 코워크가 README 내용을 자동으로 실행 설정에 반영하지는 않습니다.
- 다만 사람이 읽어서 설정할 때 참고용으로 매우 유용합니다.
- AI 에이전트가 파일을 열어볼 수는 있지만, 자동 규칙으로 항상 적용되는 파일은 아닙니다.

## 5. 서버 실행

```bash
amadeus-mcp
```

또는:

```bash
python -m amadeus_mcp.server
```

## 6. Claude 코워크 MCP 등록 예시

Claude MCP 설정 파일에 아래 형태로 추가:

```json
{
  "mcpServers": {
    "amadeus-flight": {
      "command": "python",
      "args": ["-m", "amadeus_mcp.server"],
      "cwd": "E:\\amadeus_ai",
      "env": {
        "AMADEUS_CLIENT_ID": "YOUR_ID",
        "AMADEUS_CLIENT_SECRET": "YOUR_SECRET",
        "AMADEUS_HOST": "https://api.amadeus.com",
        "MAX_REQUESTS_PER_MINUTE": "12",
        "MAX_REQUESTS_PER_DAY": "250",
        "CACHE_TTL_SECONDS": "20",
        "PRICE_CACHE_TTL_SECONDS": "15",
        "ALLOW_STALE_CACHE_ON_LIMIT": "true"
      }
    }
  }
}
```

## 7. 제공 MCP 도구

- `quota_status`: 현재 분/일 사용량, 제한값, 캐시 설정 확인
- `search_flight_offers`: 항공권 검색 + 요약 행 반환
- `search_flights_excel_rows`: Excel용 행 데이터 중심 반환
- `price_flight_offer`: 선택한 오퍼 1건 재가격 조회
- `search_and_price_top`: 검색 후 상위 N건(최대 3건)만 재가격

## 8. Excel 연동 실사용 흐름 (권장)

1. `search_flights_excel_rows`로 노선/날짜 기준 표 생성
2. Excel에서 원하는 행(오퍼) 선택
3. 선택한 오퍼만 `price_flight_offer` 호출해 최신 운임 확인
4. 예약 직전 재조회 1회로 최종 확인

핵심:
- 전체 오퍼 반복 재가격은 피하고, 선택 오퍼만 재가격
- `search_and_price_top` 사용 시 `top_n=1~2` 권장

## 9. 개인 사용자 비용/쿼터 운영 가이드

- `MAX_REQUESTS_PER_MINUTE`: 8~15
- `MAX_REQUESTS_PER_DAY`: 150~300
- `CACHE_TTL_SECONDS`: 15~30
- `PRICE_CACHE_TTL_SECONDS`: 10~20

실패 시 동작:
- 쿼터 초과 시 예외 발생
- `ALLOW_STALE_CACHE_ON_LIMIT=true`이면 만료 캐시 임시 반환 가능

## 10. 빠른 점검

```bash
python -m compileall src
python -c "from amadeus_mcp.server import mcp; print('mcp import ok')"
```

## 11. 참고

- 패키지 엔트리포인트: `pyproject.toml`의 `amadeus-mcp`
- 코드 위치: `src/amadeus_mcp/`
