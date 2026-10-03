import { useState } from "react";
import applicationLicense from "../../../LICENSE?raw";
import thirdPartyNoticesUrl from "../../../THIRD-PARTY-NOTICES.txt?url";
import { SettingsSection, SettingsPage } from "./SettingsPrimitives";

export function AboutSettingsPanel() {
  const [notices, setNotices] = useState<string | null>(null);
  const [noticeError, setNoticeError] = useState<string | null>(null);

  const loadNotices = async () => {
    if (notices !== null) return;
    try {
      const response = await fetch(thirdPartyNoticesUrl);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setNotices(await response.text());
    } catch {
      setNoticeError("ライセンス通知を読み込めませんでした。");
    }
  };

  return (
    <SettingsPage
      title="このアプリについて"
      description="Meeting Supporter本体と、利用する第三者ソフトウェアのライセンス情報です。"
    >
      <SettingsSection
        title="Meeting Supporter"
        description="Copyright © 2026 Meeting Supporter contributors"
      >
        <p className="mb-4 text-sm leading-relaxed text-ink-muted">
          GNU Affero General Public License
          v3.0の条件で再配布・改変できます。本ソフトウェアは、法律で認められる範囲で無保証です。詳しい条件は以下のライセンス全文を確認してください。
        </p>
        <details>
          <summary className="cursor-pointer text-sm font-semibold text-primary hover:text-primary-hover">
            アプリケーションライセンス（AGPL-3.0-only）
          </summary>
          <pre
            data-testid="application-license"
            className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-xl bg-ink p-4 font-mono text-xs leading-relaxed text-surface"
          >
            {applicationLicense}
          </pre>
        </details>
      </SettingsSection>

      <SettingsSection
        title="第三者ソフトウェア"
        description="フロントエンド、Rust、文書変換用Pythonワーカーと、同梱するライブラリ・モデルの通知を収録しています。"
      >
        <details
          onToggle={(event) => {
            if (event.currentTarget.open) void loadNotices();
          }}
        >
          <summary className="cursor-pointer text-sm font-semibold text-primary hover:text-primary-hover">
            THIRD-PARTY-NOTICESを表示
          </summary>
          <pre
            className="mt-3 max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-xl bg-ink p-4 font-mono text-xs leading-relaxed text-surface"
            data-testid="third-party-notices"
          >
            {notices ?? noticeError ?? "読み込み中..."}
          </pre>
        </details>
      </SettingsSection>
    </SettingsPage>
  );
}
