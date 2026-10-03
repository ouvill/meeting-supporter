# UI Documentation

画面責務、state、一般向けcopyの唯一のactive authorityは [Product Surfaces](./product-surfaces.md) である。

UI文書はPRDのrequirementとavailabilityを画面へ具体化する。backend architecture、実装task、進捗、test evidenceは置かない。

## Review Gate

- loading / empty / ready / disabled / error / cancelledを必要範囲で定義する
- availability / readiness / selectableを画面側で推測しない
- AI設定にサービス名、モデル選択、対応するAPI接続をまとめる。カスタムモデルと接続先はそのサービス内の詳細で扱い、保存済みcredential、command、runtime診断は表示しない
- アカウント機能とhosted serviceの選択肢を表示しない
- raw exception、prompt、stderr、token、credentialを表示しない
- keyboard/focusと色以外の状態表現を確認する

実装進捗と公開可能なbug・featureは[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)で管理する。
