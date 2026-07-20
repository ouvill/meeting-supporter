# ADR-012: OS native window chromeと永続する前面固定設定を採用する

- **Status**: Accepted
- **Date**: 2026-07-15
- **Builds on**: ADR-003

## Context

main windowとassistant windowの独自title barは、OS標準のwindow操作、keyboard、screen reader、drag領域、platform固有挙動を再実装していた。アプリ内navigation/statusと、minimize・maximize・restore・closeというwindow managerの責務も同じbarに混在していた。

assistantは会議中に再表示できる補助windowであり、closeで破棄すべきではない。一方、mainを閉じた後にhidden assistantだけがprocessを残す挙動も避ける必要がある。assistantの前面固定は利用者が明示的に選べ、再起動後も同じ希望値を適用する必要がある。

## Decision

### 1. mainとassistantでOS native window barを使う

Tauriのmain/assistant両windowを`decorations: true`とする。mainのnative titleは「会議支援AI」、assistantは「ライブ返答支援」とする。minimize、maximize、restore、window drag、native close controlはOSへ委ね、HTMLで再実装しない。

アプリ固有のnavigation、meeting status、設定、前面固定はnative bar直下のapp toolbarへ置く。app toolbarをwindow drag領域やnative controlの代替にしない。

### 2. native closeの意味をwindowごとに分ける

- mainの`CloseRequested`は既定closeをpreventし、アプリprocessを終了する。
- assistantの`CloseRequested`は既定closeをpreventし、windowをhideする。windowを破棄しない。

これによりassistantは同じsession stateのまま再表示でき、mainを閉じた後にassistantだけがprocessを保持しない。

### 3. 前面固定はdesired preferenceとactual stateを分離する

mainとassistantは互いに独立した前面固定stateを持つ。

- main: default OFF、永続化しない。
- assistant: default ON、`meeting-supporter.assistant-always-on-top`へ利用者のdesired preferenceを保存する。

UIが示す`pinned`はTauriからreadbackしたactual stateだけとし、確認前はunknownを表示する。保存値やdefaultをactual stateとして推測しない。

assistant起動時は保存値`"true"`または`"false"`だけを受理し、欠損、不正、読取例外ではdesiredをONとする。desiredを適用し、readbackが一致した場合だけ保存値を正規化する。apply/readback失敗時は保存済みdesiredを上書きしない。storage書込失敗でもactual stateは維持し、安全なstatusで永続化失敗を伝える。

### 4. hide/showと再起動の扱い

assistantのhide/showではmounted component stateを維持する。アプリ再起動時は保存済みdesired preferenceを再適用し、actual readbackでUIを更新する。mainとassistantでstorage keyや推測stateを共有しない。

## Rejected Alternatives

### 独自title barを継続する

OSごとのwindow semantics、accessibility、keyboard、hit targetを再実装し続ける必要があり、app toolbarの責務も曖昧になるため採用しない。

### assistant closeでwindowを破棄する

再表示時にstateを失い、会議中の補助windowという契約を壊すため採用しない。

### main closeで通常のwindow closeだけを行う

hidden assistantがprocessを残す可能性があるため採用しない。

### 保存値をactual pin stateとして表示する

window managerまたはAPIの失敗時に、実状態と異なる表示を成功として示すため採用しない。

## Consequences

### Benefits

- OS標準のwindow操作、keyboard、screen reader semanticsを利用できる。
- app toolbarはproduct navigation/statusだけに集中できる。
- assistant close後も再表示でき、main closeではprocessが確実に終了する。
- pinの保存希望と実window stateの不一致を正直に扱える。

### Costs and risks

- native chromeの外観と挙動はOSごとに異なり、各対応OSの実shell検証が必要になる。
- assistant close policyとmain exit policyをRust側で維持する必要がある。
- storage、Tauri apply、readbackの各失敗を分けたUI状態が必要になる。

## Supersession

既存ADRのarchitecture boundaryは置き換えない。本ADRはmain/assistant window chrome、native close policy、前面固定preferenceの現行authorityである。

## Related Documents

- [Product Requirements](../product/prd.md)
- [Product Surfaces](../ui/product-surfaces.md)
- [GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)
