# 무료 운영을 위한 아키텍처 재설계

## 결정 요약

현재 Python 프로세스는 Discord Gateway를 계속 연결하고 SQLite 파일을 보존해야 합니다. Render 무료 web service는 15분간 수신 트래픽이 없으면 휴면하고, 로컬 파일도 재시작·재배포·휴면 때 사라지므로 이 실행 방식과 맞지 않습니다. 무료 PostgreSQL도 30일 뒤 만료됩니다. 따라서 기존 봇을 Render Free에 억지로 유지하는 대신, 상시 서버가 필요 없는 상호작용형 구조로 옮기는 것을 최종 무료 운영안으로 삼습니다.

| 역할 | 무료 대상 구성 | 책임 |
| --- | --- | --- |
| Discord 명령·온보딩 | Cloudflare Worker HTTP Interactions endpoint | 서명 검증, `/온보딩`·관심사·설정·프로그램 응답 |
| 영속 데이터 | Cloudflare D1 | 사용자 관심사, 프로그램, 중복 발송 방지 기록 |
| 수집 스케줄 | GitHub Actions (6시간 간격) | 현재 Python SSU Job/SSU-PATH 크롤러 실행 및 Worker의 인증된 수집 API 호출 |
| 알림 발송 | Actions + Worker outbox API 또는 제한된 Worker 배치 | 신규 프로그램 매칭 후 Discord DM, 발송 성공·실패 기록 |

Discord는 Gateway 연결 대신 HTTP outgoing interactions로 명령을 전달할 수 있습니다. 따라서 상시 실행 프로세스를 없애고, 봇 기능은 요청 때만 실행하며 크롤링만 정기 작업으로 분리합니다. Cloudflare Cron은 무료 CPU가 10ms이므로 HTML 파싱 작업을 넣지 않습니다. Python 수집기는 별도 재작성 없이 GitHub Actions runner에서 계속 쓸 수 있습니다.

## 동작 흐름

1. Discord가 상호작용 요청을 Cloudflare Worker로 보냅니다. Worker는 Discord의 Ed25519 서명을 검증하고 D1을 통해 관심사와 프로그램을 조회·갱신합니다.
2. 6시간마다 GitHub Actions가 기존 비동기 Python 크롤러로 두 SSU 사이트를 수집합니다. 스케줄은 정각을 피하고, 수동 실행도 허용합니다.
3. Actions는 Worker 내부 API에 별도 비밀 토큰과 함께 새 프로그램 목록을 전송합니다. Worker는 D1에 upsert하고 사용자별 알림 outbox를 만듭니다.
4. outbox를 제한된 묶음으로 읽어 Discord DM을 전송하고, 성공/실패를 기록합니다. 동일 프로그램은 같은 사용자에게 두 번 발송하지 않습니다.

Discord의 초기 interaction 응답은 3초 이내여야 하므로, 대량 수집·DM은 사용자 명령 요청 안에서 기다리지 않게 합니다. Discord bot token, Discord public key, 내부 ingest secret은 각각 필요한 실행환경의 secret으로만 저장합니다. D1에는 Discord ID, 관심사, 프로그램과 발송 내역만 보관합니다. 비동기 DM을 받으려면 사용자가 봇과 공통 서버를 가지고 DM을 허용해야 하므로, 온보딩 시 이를 안내합니다.

## 무료 한도와 안전장치

- Cloudflare Workers Free는 하루 100,000 요청, HTTP 요청당 CPU 10ms이며, D1 Free는 하루 5백만 행 읽기와 100,000 행 쓰기, 총 5GB 저장 한도입니다. D1 일일 한도를 넘으면 초과분이 유료로 전환되는 대신 쿼리가 오류로 실패하므로, 알림이 지연될 수 있습니다.
- GitHub Free private repository는 표준 runner 2,000분/월을 포함합니다. 6시간마다 한 번 실행하고 Python 수집을 5분 안에 끝낸다고 가정하면 약 600분/월입니다. 실제 실행 시간을 측정해 상한을 조정합니다. 결제 한도 초과로 청구하지 않도록 유료 사용을 활성화하지 않고, 무료 분량 소진 때 작업이 중단되는 fail-closed 동작을 유지합니다.
- GitHub 예약 실행은 혼잡 시 지연되거나 일부가 누락될 수 있고 기본 브랜치에서만 실행됩니다. 매 실행마다 직전 성공 시각, 소스별 수집 건수, 오류를 남기며 `/workflow_dispatch` 수동 재실행을 제공합니다.
- Worker handler는 가벼운 interaction 처리만 담당합니다. Discord 서명 검증, 표시용 데이터 변환, 명령 응답을 10ms CPU 안에서 검증하고, 사용자/공고가 늘어날 때 D1 행 사용량과 Worker CPU를 먼저 측정합니다.
- 도메인 구입, Render 유료 worker, Cloudflare Workers Paid, 상시 VM 비용은 필수 구성에서 제외합니다. Cloudflare `workers.dev` 주소와 GitHub 기본 Actions runner를 사용합니다.

## 버리지 않는 대안

Oracle Cloud는 Always Free A1 컴퓨트(최대 2 OCPU/12GB)를 제공해 현재 Python Gateway 봇을 VM에서 돌릴 수는 있습니다. 다만 Oracle은 7일 동안 CPU 95백분위·네트워크·A1 메모리 사용량이 모두 20% 미만인 무료 인스턴스를 회수할 수 있습니다. 이처럼 부하가 작은 Discord 봇은 그 조건에 들어갈 수 있고, 가입 때 카드 인증이나 지역 내 용량도 필요할 수 있습니다. 따라서 개인 실험·자가 관리 VM의 선택지로만 두고, 서비스의 신뢰 가능한 기본 무료안으로 삼지 않습니다.

## 단계적 전환

1. 완료: SSU-PATH는 계정 로그인이 아닌 공개 모집공고 목록을 사용하도록 수정했습니다. GitHub Actions에서 쓸 수 있는 순수 Python 수집기를 유지합니다.
2. 다음 작업: Cloudflare Worker/D1 상호작용 endpoint, migration, Discord 명령 등록 및 로컬 테스트를 구현합니다.
3. 다음 작업: GitHub Actions 6시간 수집 workflow, 내부 ingest/outbox API, 재시도·중복 방지 및 실행 로그를 붙입니다.
4. 실제 Cloudflare 계정과 Discord 앱에서 endpoint 검증·초기 배포 후 기존 Gateway 봇을 끕니다. 이 저장소 작업만으로 외부 계정이 생성되거나 운영 배포되지는 않습니다.

## 참고 자료

- [Render Free 한도와 파일시스템 정책](https://render.com/docs/free)
- [Oracle Cloud Free Tier](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm), [Always Free 리소스 및 유휴 VM 회수 기준](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Discord interactions 수신/응답](https://docs.discord.com/developers/interactions/receiving-and-responding), [Discord application commands](https://docs.discord.com/developers/docs/interactions/slash-commands)
- [Cloudflare Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [D1 pricing and free limits](https://developers.cloudflare.com/d1/platform/pricing/)
- [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [scheduled workflow 지연·요건](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
