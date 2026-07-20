# UI Documentation

画面責務、state、一般向けcopyの唯一のactive authorityは [Product Surfaces](./product-surfaces.md) である。

UI文書はPRDのrequirementとavailabilityを画面へ具体化する。backend architecture、実装task、進捗、test evidenceは置かない。

## Review Gate

- loading / empty / ready / disabled / error / cancelledを必要範囲で定義する
- availability / readiness / selectableを画面側で推測しない
- 一般設定にprovider/service名、route card、provider固有のAPI credential controlを表示してよいが、保存済み値、model識別子、endpoint、command、runtime診断は出さない
- 通常のOSS buildで未設定のhosted serviceを選択可能に見せない
- raw exception、prompt、stderr、token、credentialを表示しない
- keyboard/focusと色以外の状態表現を確認する

実装進捗と公開可能なbug・featureは[GitHub Issues](https://github.com/ouvill/meeting-supporter/issues)で管理する。
