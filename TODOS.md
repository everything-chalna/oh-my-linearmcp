# oh-my-linear TODOs

## Phase 1: UX 핵심 개선 (Done)

### 1. MCP Reconnect 시 캐시 자동 갱신 -- Done
- **구현:** Idle-gap 휴리스틱. `reader.py`의 `ensure_fresh()` 메서드가 마지막 tool call 이후 60초 이상 경과 시 `_force_next_refresh = True` 설정. `router.py`의 `call_read()`/`call_official()` 첫 줄에서 호출하여 25+ 도구 전체 적용.
- **환경변수:** `LINEAR_FAST_IDLE_REFRESH_SECONDS` (기본 60)
- **테스트:** `tests/test_reader_idle_refresh.py` (10 tests), `tests/test_router_idle_refresh.py` (4 tests)

### 2. failure_count 리셋 -- Done
- **구현:** `reader.py`의 `_set_healthy()`에서 `self._health.failure_count = 0` 리셋 (이미 구현됨)
- **테스트:** `tests/test_reader_health.py`

### 3. 21개 로컬 Tool Docstring 추가 -- Done
- **구현:** `server.py`의 21개 `@mcp.tool()` 함수에 개별 docstring 작성 완료 (이미 구현됨)

### 4. Notion OAuth 재인증 도구 -- Done
- **구현:** `official_session.py`에 `clear_token_cache_for_url()` static method 추가. `router.py`에 `reauth_notion()`, `reauth_all()` 추가. `server.py`에 `reauth_notion`, `reauth_all` MCP 도구 정의.
- **환경변수:** `NOTION_OFFICIAL_MCP_URL` (기본 `https://mcp.notion.com/mcp`)
- **테스트:** `tests/test_reauth_notion.py` (12 tests)

---

## Phase 2: 안정성 개선 (Deferred)

### 4. Stale 데이터 메타데이터 플래그
- **What:** fallback으로 stale 데이터 반환 시 응답에 `_metadata.stale: true` 추가
- **Why:** 현재 stale 데이터가 fresh 데이터와 동일한 구조로 반환됨. 사용자/Claude가 데이터 신선도를 판단할 수 없음
- **Context:**
  - `router.py:110, 123`에서 `allow_degraded=True`로 stale 반환하는 2개 코드패스
  - `local_handlers.py`의 반환 dict에 `_metadata` 키 주입 방식 검토 필요
  - 응답 contract 변경이므로 하위 호환성 고려 필요
- **Depends on:** 없음

### 5. reader.py + store_detector.py 테스트 추가
- **What:** 코드의 41%를 차지하는 핵심 모듈(reader.py 1121줄, store_detector.py 210줄)에 단위 테스트 추가
- **Why:** IndexedDB 파싱, health 상태 전환, account scope 적용, store shape matching 등 핵심 로직이 미테스트. Linear.app 업데이트로 DB 포맷이 바뀌면 silent regression 발생 가능
- **Context:**
  - 현재 32개 테스트가 있지만 reader/store_detector는 미커버
  - Mock LevelDB 데이터로 테스트하거나, fixture 파일로 실제 DB 스냅샷 사용
  - `conftest.py`의 `MiniReader` fixture를 확장하여 reader 초기화 테스트 가능
  - 테스트 대상: `_reload_cache()`, `_load_from_store()`, `detect_stores()`, `_set_degraded()`/`_set_healthy()` 상태 전환, `_apply_account_scope()`
- **Depends on:** 없음

### 6. GitHub Actions CI 파이프라인
- **What:** PR/push 시 자동 pytest + ruff 실행하는 GitHub Actions workflow 추가
- **Why:** 현재 테스트/린팅이 수동 실행에 의존. Breaking change가 검증 없이 머지될 수 있음
- **Context:**
  - `.github/workflows/ci.yml` 신규 생성
  - `uv sync --group dev && uv run pytest && uv run ruff check` 실행
  - Python 3.10+ 매트릭스 테스트 권장
  - pre-commit hook도 함께 고려 가능 (`.pre-commit-config.yaml`)
- **Depends on:** #5 (테스트가 충분해야 CI가 의미 있음)
