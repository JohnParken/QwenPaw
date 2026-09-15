import { Badge, Spin } from "antd";
import { CheckOutlined, CopyOutlined, TagOutlined } from "@ant-design/icons";
import { Button, Modal } from "@agentscope-ai/design";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import api from "../api";
import { ExternalMarkdownLink } from "../components/Markdown/externalLinkComponents";
import { useTheme } from "../contexts/ThemeContext";
import { Slot } from "../plugins/registry/Slot";
import { openExternalLink } from "../utils/openExternalLink";
import {
  compareVersions,
  getReleaseNotesUrl,
  isStableVersion,
  ONE_HOUR_MS,
  PYPI_URL,
  UPDATE_MD,
} from "./constants";
import styles from "./index.module.less";

function UpdateCodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className={styles.codeBlock}>
      <code className={styles.codeBlockInner}>{code}</code>
      <button
        className={`${styles.copyBtn} ${
          copied ? styles.copyBtnCopied : styles.copyBtnDefault
        }`}
        onClick={handleCopy}
        title="Copy"
      >
        {copied ? <CheckOutlined /> : <CopyOutlined />}
      </button>
    </div>
  );
}

interface AppBrandProps {
  action?: ReactNode;
  hidden?: boolean;
  version?: string;
}

export default function AppBrand({
  action,
  hidden = false,
  version: versionProp,
}: AppBrandProps) {
  const { t, i18n } = useTranslation();
  const { isDark } = useTheme();
  const [loadedVersion, setLoadedVersion] = useState("");
  const version = versionProp ?? loadedVersion;
  const [latestVersion, setLatestVersion] = useState("");
  const [updateModalOpen, setUpdateModalOpen] = useState(false);
  const [updateMarkdown, setUpdateMarkdown] = useState("");

  useEffect(() => {
    if (versionProp !== undefined) return;
    void api
      .getVersion()
      .then((response) => setLoadedVersion(response?.version ?? ""))
      .catch(() => {});
  }, [versionProp]);

  useEffect(() => {
    fetch(PYPI_URL)
      .then((response) => response.json())
      .then((data) => {
        const releases = data?.releases ?? {};
        const versionsWithTime = Object.entries(releases)
          .filter(([candidate]) => isStableVersion(candidate))
          .map(([candidate, files]) => {
            const fileList = files as Array<{
              upload_time_iso_8601?: string;
            }>;
            const latestUpload = fileList
              .map((file) => file.upload_time_iso_8601)
              .filter(Boolean)
              .sort()
              .pop();
            return {
              version: candidate,
              uploadTime: latestUpload || "",
            };
          });

        versionsWithTime.sort((left, right) => {
          const timeDiff =
            new Date(right.uploadTime).getTime() -
            new Date(left.uploadTime).getTime();
          return timeDiff !== 0
            ? timeDiff
            : compareVersions(right.version, left.version);
        });

        const latest =
          versionsWithTime[0]?.version ?? data?.info?.version ?? "";
        const releaseTime = versionsWithTime.find(
          (item) => item.version === latest,
        )?.uploadTime;
        const isOldEnough =
          !!releaseTime &&
          new Date(releaseTime) <= new Date(Date.now() - ONE_HOUR_MS);
        setLatestVersion(isOldEnough ? latest : "");
      })
      .catch(() => {});
  }, []);

  const hasUpdate =
    !!version &&
    !!latestVersion &&
    compareVersions(latestVersion, version) > 0;
  const modalVersion = latestVersion;
  const handleOpenUpdateModal = () => {
    setUpdateMarkdown("");
    setUpdateModalOpen(true);
    const language = i18n.language?.startsWith("zh")
      ? "zh"
      : i18n.language?.startsWith("ru")
      ? "ru"
      : "en";

    const faqLanguage = language === "zh" ? "zh" : "en";
    fetch(`https://qwenpaw.agentscope.io/docs/faq.${faqLanguage}.md`, {
      cache: "no-cache",
    })
      .then((response) => (response.ok ? response.text() : Promise.reject()))
      .then((text) => {
        const zhPattern = /###\s*QwenPaw如何更新[\s\S]*?(?=\n###|$)/;
        const enPattern = /###\s*How to update QwenPaw[\s\S]*?(?=\n###|$)/;
        const match = text.match(faqLanguage === "zh" ? zhPattern : enPattern);
        setUpdateMarkdown(
          match && language !== "ru"
            ? match[0].trim()
            : UPDATE_MD[language] ?? UPDATE_MD.en,
        );
      })
      .catch(() => {
        setUpdateMarkdown(UPDATE_MD[language] ?? UPDATE_MD.en);
      });
  };

  const versionContent = (
    <span className={styles.appBrandVersionArea}>
      {version && (
        <Badge
          dot={hasUpdate}
          color="rgba(255, 157, 77, 1)"
          offset={[3, 1]}
        >
          <span
            className={`${styles.versionBadge} ${
              hasUpdate ? styles.versionBadgeClickable : ""
            }`}
            onClick={hasUpdate ? handleOpenUpdateModal : undefined}
          >
            v{version}
          </span>
        </Badge>
      )}
    </span>
  );

  return (
    <>
      <div className={styles.appBrand} hidden={hidden}>
        <span className={styles.appBrandLogo}>
          <Slot name="header.logo" kind="replace">
            <img
              src={isDark ? "/logo-dark.svg" : "/logo-light.svg"}
              alt="QwenPaw"
              className={styles.logoImg}
            />
          </Slot>
        </span>
        <span className={styles.logoDivider} />
        {versionContent}
        {action && <span className={styles.appBrandAction}>{action}</span>}
      </div>

      <Modal
        title={null}
        open={updateModalOpen}
        onCancel={() => setUpdateModalOpen(false)}
        footer={[
          <Button key="close" onClick={() => setUpdateModalOpen(false)}>
            {t("common.close")}
          </Button>,
          <Button
            key="releases"
            type="primary"
            className={styles.updateViewReleasesBtn}
            onClick={() => openExternalLink(getReleaseNotesUrl(i18n.language))}
          >
            {t("sidebar.updateModal.viewReleases")}
          </Button>,
        ]}
        width={960}
        className={styles.updateModal}
      >
        <div className={styles.updateModalBanner}>
          <div className={styles.updateModalBannerLeft}>
            <span className={styles.updateModalVersionTag}>
              <TagOutlined />
              Version {modalVersion || version}
            </span>
            <div className={styles.updateModalBannerTitle}>
              {t("sidebar.updateModal.title", {
                version: modalVersion || version,
              })}
            </div>
          </div>
        </div>

        <div className={styles.updateModalBody}>
          {updateMarkdown ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ExternalMarkdownLink,
                code({ node, className, children, ...props }: any) {
                  const match = /language-(\w+)/.exec(className || "");
                  const isBlock =
                    node?.position?.start?.line !== node?.position?.end?.line ||
                    match;
                  return isBlock ? (
                    <UpdateCodeBlock
                      code={String(children).replace(/\n$/, "")}
                    />
                  ) : (
                    <code className={styles.codeInline} {...props}>
                      {children}
                    </code>
                  );
                },
              }}
            >
              {updateMarkdown}
            </ReactMarkdown>
          ) : (
            <div className={styles.updateModalSpinWrapper}>
              <Spin />
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
