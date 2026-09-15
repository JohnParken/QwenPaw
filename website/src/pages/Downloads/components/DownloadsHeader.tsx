import { useTranslation } from "react-i18next";
import { DOWNLOADS_HEADER_BG_URL } from "../constants";

export function DownloadsHeader() {
  const { t } = useTranslation();

  return (
    <header className="relative -mt-4 mb-10 pt-8 text-center sm:-mt-6 lg:-mt-8">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 left-[calc(50%-50vw)] -z-10 w-screen bg-cover bg-bottom bg-no-repeat"
        style={{ backgroundImage: `url(${DOWNLOADS_HEADER_BG_URL})` }}
      />
      <h1 className="relative mb-3 mt-8 text-4xl font-bold tracking-tight text-site-text md:text-[2.75rem]">
        {t("downloads.title")}
      </h1>
      <p className="relative mb-10 text-base text-site-text-muted md:text-lg">
        {t("downloads.subtitle")}
      </p>
    </header>
  );
}
