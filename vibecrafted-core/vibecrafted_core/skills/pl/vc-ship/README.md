# vc-ship

Parasol cyklu życia: jedna misja przeleciana przez wszystkie jedenaście etapów
kadencji Read-Write (scaffold → … → release) jako pojedynczy nadzorowany run.
Wywołujący agent staje się nadzorującym operatorem runu — weryfikuje raport
każdego etapu, steruje czasownikami human-controls (approve / interrupt /
fallback / force-audit / accept-dou) i niesie pałeczkę wraz z ładunkiem raportów
aż do release'u. Sięgnij po to, gdy cięcie produktowe zasługuje na pełną
kadencję; dostajesz z powrotem otrasowany run cyklu życia, raporty per etap i
uczciwy końcowy raport z lotu ze śladem DoU.

## Szybka ściąga

| Pole                | Wartość                    |
| ------------------- | -------------------------- |
| Nazwa               | `vc-ship`                  |
| Wersja              | `1.0.0`                    |
| Komenda operatora   | `vibecrafted ship <agent>` |
| Skrót shellowy      | `vc-ship <agent>`          |
| Dokument kanoniczny | [`SKILL.md`](SKILL.md)     |

## Kanon powiązany

- [`docs/runtime/LIFECYCLE.md`](../../docs/runtime/LIFECYCLE.md) — kadencja
  Read-Write, architektura komponentów i model nadzoru.
- [`docs/runtime/AGENT_OPS.md`](../../docs/runtime/AGENT_OPS.md) — klasy awarii,
  które musi znać każdy nadzorca (gate-nap, report-on-death), oraz sprawdzone w
  boju wzorce watcherów.
