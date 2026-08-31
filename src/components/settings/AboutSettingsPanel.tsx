import { useCallback, useState } from "react";
import thirdPartyNoticesUrl from "../../../THIRD-PARTY-NOTICES.txt?url";
import { SettingsCard, SettingsPage } from "./SettingsPrimitives";

interface DeferredText {
  content: string | null;
  error: string | null;
  loading: boolean;
}

async function loadApplicationLicenseText(): Promise<string> {
  // This import boundary keeps the full license out of the initial Settings chunk.
  const licenseModule = await import("../../../LICENSE?raw");
  return licenseModule.default;
}

async function loadThirdPartyNoticesText(): Promise<string> {
  const response = await fetch(thirdPartyNoticesUrl);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.text();
}

function useDeferredText(
  loadText: () => Promise<string>,
  errorMessage: string,
) {
  const [state, setState] = useState<DeferredText>({
    content: null,
    error: null,
    loading: false,
  });

  const load = useCallback(async () => {
    if (state.content !== null || state.loading) return;
    setState({ content: null, error: null, loading: true });
    try {
      const content = await loadText();
      setState({ content, error: null, loading: false });
    } catch {
      setState({ content: null, error: errorMessage, loading: false });
    }
  }, [errorMessage, loadText, state.content, state.loading]);

  return {
    load,
    text: state.content ?? state.error ?? "読み込み中...",
  };
}

export function AboutSettingsPanel() {
  const applicationLicense = useDeferredText(
    loadApplicationLicenseText,
    "アプリケーションライセンスを読み込めませんでした。",
  );
  const thirdPartyNotices = useDeferredText(
    loadThirdPartyNoticesText,
    "ライセンス通知を読み込めませんでした。",
  );

  return (
    <SettingsPage
      title="このアプリについて"
      description="Meeting Supporter本体と、利用する第三者ソフトウェアのライセンス情報です。"
    >
      <SettingsCard
        title="Meeting Supporter"
        description="Copyright © 2026 Meeting Supporter contributors"
      >
        <p className="mb-4 text-sm leading-relaxed text-ink-muted">
          GNU Affero General Public License
          v3.0の条件で再配布・改変できます。本ソフトウェアは、法律で認められる範囲で無保証です。詳しい条件は以下のライセンス全文を確認してください。
        </p>
        <details
          onToggle={(event) => {
            if (event.currentTarget.open) void applicationLicense.load();
          }}
        >
          <summary className="cursor-pointer text-sm font-semibold text-primary hover:text-primary-hover">
            アプリケーションライセンス（AGPL-3.0-only）
          </summary>
          <pre
            data-testid="application-license"
            className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-xl bg-ink p-4 font-mono text-xs leading-relaxed text-surface"
          >
            {applicationLicense.text}
          </pre>
        </details>
      </SettingsCard>

      <SettingsCard
        title="第三者ソフトウェア"
        description="Node.js、Rust、Pythonの依存関係と、初回起動時に公式配布元から取得するuvの通知を収録しています。"
      >
        <details
          onToggle={(event) => {
            if (event.currentTarget.open) void thirdPartyNotices.load();
          }}
        >
          <summary className="cursor-pointer text-sm font-semibold text-primary hover:text-primary-hover">
            THIRD-PARTY-NOTICESを表示
          </summary>
          <pre
            className="mt-3 max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-xl bg-ink p-4 font-mono text-xs leading-relaxed text-surface"
            data-testid="third-party-notices"
          >
            {thirdPartyNotices.text}
          </pre>
        </details>
      </SettingsCard>
    </SettingsPage>
  );
}
