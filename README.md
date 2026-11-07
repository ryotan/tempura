# repository-standards

Renovateの共通プリセット、EditorConfigの配布元、GitHub Actionsの検査ワークフローを管理します。
初回試行用の構成です。GitHub Actionsでの実行とMend-hosted Renovateからの更新は、公開後に検証します。

## 利用方法

| 対象 | 利用側で管理するもの | 更新方法 |
|---|---|---|
| Renovate | 共通プリセットの参照と製品固有のルール | Renovateのプリセット参照更新 |
| EditorConfig | `.editorconfig`とCopierのanswersファイル | RenovateのCopier更新 |
| ワークフロー検査 | 共有ワークフローを呼ぶ小さなYAML | RenovateのGitHub Actions更新 |

以下は公開先を`ryotan/repository-standards`とした例です。
`VERSION`を検証済みのバージョンタグ、`COMMIT_SHA`をその完全なコミットSHAに置き換えてください。
参照は別々に更新されるため、常に同じバージョンになるとは限りません。

### RenovateとEditorConfig

利用側のRenovate設定から共通プリセットを参照します。製品固有のグループや例外は利用側に残します。

```json5
{
  "extends": [
    "github>ryotan/repository-standards:default.json5#VERSION",
    "github>ryotan/repository-standards:copier.json5#VERSION"
  ]
}
```

`copier.json5`は、EditorConfigの配布テンプレートを使うリポジトリで追加します。
この実行版の指定は、同じリポジトリ内のすべてのCopierテンプレートに適用されます。
既存のテンプレートがある場合は、指定版との互換性を導入前に確認してください。

初回のファイルは、このリポジトリのValidateワークフローをバージョンタグで手動実行して生成します。
ワークフローがデフォルトブランチに公開されてから実行できます。

```sh
gh workflow run validate.yaml --repo ryotan/repository-standards --ref VERSION
```

実行全体の成功を確認し、その`RUN_ID`の成果物を作業用ディレクトリに取得します。

```sh
gh run download RUN_ID --repo ryotan/repository-standards --name shared-settings --dir shared-settings
```

成果物は`.editorconfig`と`.copier-answers.shared-settings.yml`の2ファイルです。
既存の内容と比較し、理由のある差分を解決してから導入PRを作成します。
未確認のローカル変更を成果物で上書きしないでください。
この手順では、手元へのPython・Copierのインストールは不要です。

### ワークフロー検査

利用側に次のような呼び出し用ワークフローを置きます。
製品のビルド・テストと、その必須チェック名は引き続き利用側で管理します。

```yaml
name: Workflow Lint
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
concurrency:
  group: workflow-lint-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  workflow-lint:
    name: Workflow Lint
    uses: ryotan/repository-standards/.github/workflows/workflow-lint.yaml@COMMIT_SHA # VERSION
```

共有ワークフローは、参照したコミットのmise設定からjactionlintを導入します。
追跡済みのワークフローとActionメタデータを検査し、ShellCheck・Python解析は対象に含めません。
共通のjactionlint設定を明示して実行するため、利用側の設定ファイルの継承は現在の対象外です。
既存設定や個別事情がある場合は導入前に確認し、無視コメントも理由をレビューしてください。

checkoutの`persist-credentials: false`やegress設定は、初回の導入PRでも確認します。
共通規約の欠落や変更をすべて自動検出する検査は、今後の対象です。

## 更新と復旧

共通側で検査済みのバージョンを公開すると、Renovateが利用側の更新PRを作成します。
Copierの実行版を上げる場合は、旧テンプレートからの更新を検証し、実行版の参照から先に採用します。

- 競合や生成エラーがあるPRは、警告と差分を確認して解決するまでマージしません。
- 修正後はRenovateのrebase・retryを明示的に要求します。
- 不正な更新は、EditorConfigとanswersを同じ採用状態へ戻します。既存タグは移動しません。
- ファイル削除が維持される場合もあります。answersの更新だけで修復済みとは判断しません。

Copierは競合を残して正常終了する場合があります。PRの作成やコマンドの成功だけでは採用できません。
実際の更新PRとCIを確認し、ファイル・参照・個別変更が意図どおり維持されていることをレビューします。
