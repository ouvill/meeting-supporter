import { $, browser, expect } from "@wdio/globals";

const DEFAULT_WAIT_OPTIONS = {
  timeout: 15_000,
  interval: 200,
};

interface SurfaceMetrics {
  opacity: number;
  visibility: string;
  width: number;
  height: number;
}

export async function expectDisplayedSurface(
  selector: string,
  waitOptions: WebdriverIO.WaitForOptions = DEFAULT_WAIT_OPTIONS,
): Promise<WebdriverIO.Element> {
  const surface = await $(selector);
  await surface.waitForDisplayed(waitOptions);

  const metrics = await browser.execute<SurfaceMetrics, [WebdriverIO.Element]>(
    (element) => {
      const rect = element.getBoundingClientRect();
      let opacity = 1;
      let visibility = "visible";
      let current: HTMLElement | null = element;

      while (current) {
        const style = getComputedStyle(current);
        opacity *= Number.parseFloat(style.opacity);
        if (style.visibility !== "visible") visibility = style.visibility;
        current = current.parentElement;
      }

      return {
        opacity,
        visibility,
        width: rect.width,
        height: rect.height,
      };
    },
    surface,
  );

  expect(metrics.opacity).toBeGreaterThanOrEqual(0.99);
  expect(metrics.visibility).toBe("visible");
  expect(metrics.width).toBeGreaterThan(0);
  expect(metrics.height).toBeGreaterThan(0);

  return surface;
}
