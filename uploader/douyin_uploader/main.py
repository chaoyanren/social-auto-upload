# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from playwright.async_api import Playwright, async_playwright, Page
import os
import asyncio

from conf import LOCAL_CHROME_PATH, LOCAL_CHROME_HEADLESS
from utils.base_social_media import set_init_script
from utils.log import douyin_logger


async def cookie_auth(account_file):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=LOCAL_CHROME_HEADLESS)
        context = await browser.new_context(storage_state=account_file)
        context = await set_init_script(context)
        # 创建一个新的页面
        page = await context.new_page()
        # 访问指定的 URL
        await page.goto("https://creator.douyin.com/creator-micro/content/upload")
        try:
            await page.wait_for_url("https://creator.douyin.com/creator-micro/content/upload", timeout=5000)
        except:
            print("[+] 等待5秒 cookie 失效")
            await context.close()
            await browser.close()
            return False
        # On recent Douyin creator pages, an invalid session may still stay on the same URL
        # but render a login panel. A valid session should expose a file input for upload.
        try:
            await page.locator("input[type='file']").first.wait_for(timeout=15000)
        except:
            print("[+] cookie 失效：未检测到上传控件")
            await context.close()
            await browser.close()
            return False
        # 2024.06.17 抖音创作者中心改版
        if await page.get_by_text('手机号登录').count() or await page.get_by_text('扫码登录').count():
            print("[+] 等待5秒 cookie 失效")
            return False
        else:
            print("[+] cookie 有效")
            return True


async def douyin_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            # Todo alert message
            return False
        douyin_logger.info('[+] cookie文件不存在或已失效，即将自动打开浏览器，请扫码登录，登陆后会自动生成cookie文件')
        await douyin_cookie_gen(account_file)
    return True


async def douyin_cookie_gen(account_file):
    async with async_playwright() as playwright:
        options = {
            'headless': LOCAL_CHROME_HEADLESS
        }
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://creator.douyin.com/")
        await page.pause()
        # 点击调试器的继续，保存cookie
        await context.storage_state(path=account_file)


class DouYinVideo(object):
    def __init__(self, title, file_path, tags, publish_date: datetime, account_file, thumbnail_path=None, productLink='', productTitle=''):
        self.title = title  # 视频标题
        self.file_path = file_path
        self.tags = tags
        self.publish_date = publish_date
        self.account_file = account_file
        self.date_format = '%Y年%m月%d日 %H:%M'
        self.local_executable_path = LOCAL_CHROME_PATH
        self.headless = LOCAL_CHROME_HEADLESS
        self.thumbnail_path = thumbnail_path
        self.productLink = productLink
        self.productTitle = productTitle

    async def set_schedule_time_douyin(self, page, publish_date):
        await self.clear_cover_overlays(page)
        schedule_text = "\u5b9a\u65f6\u53d1\u5e03"

        clicked_schedule = await page.evaluate(
            """(text) => {
                const labels = Array.from(document.querySelectorAll("label[class^='radio']"));
                const target = labels.find(el => el.offsetParent !== null && (el.textContent || '').includes(text));
                if (!target) return false;
                target.click();
                return true;
            }""",
            schedule_text,
        )
        if not clicked_schedule:
            label_element = page.locator(f"[class^='radio']:has-text('{schedule_text}')").first
            await label_element.click(force=True)

        await asyncio.sleep(0.8)
        publish_date_hour = publish_date.strftime("%Y-%m-%d %H:%M")

        datetime_text = "\u65e5\u671f\u548c\u65f6\u95f4"
        date_input_selectors = [
            f".semi-input[placeholder='{datetime_text}']",
            f"input[placeholder='{datetime_text}']",
            "input[placeholder*='date'][placeholder*='time']",
            "div.semi-datepicker-input",
            "div.semi-datepicker",
            "div[class*='date-picker']",
        ]
        clicked = False
        for selector in date_input_selectors:
            loc = page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(force=True, timeout=2000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("Failed to locate schedule datetime input on Douyin publish page.")

        await asyncio.sleep(0.2)
        await page.keyboard.press("Control+KeyA")
        await page.keyboard.type(str(publish_date_hour))
        await page.keyboard.press("Enter")
        await asyncio.sleep(1)

    async def _is_text_visible(self, page: Page, text: str) -> bool:
        loc = page.get_by_text(text).first
        try:
            return await loc.count() > 0 and await loc.is_visible()
        except Exception:
            return False

    async def _publish_success_detected(self, page: Page) -> bool:
        if "creator-micro/content/manage" in page.url:
            return True

        success_markers = [
            "\u53d1\u5e03\u6210\u529f",
            "\u5b9a\u65f6\u53d1\u5e03\u6210\u529f",
            "\u4f5c\u54c1\u7ba1\u7406",
            "\u7ee7\u7eed\u53d1\u5e03",
        ]
        for marker in success_markers:
            if await self._is_text_visible(page, marker):
                return True
        return False

    async def _fix_too_soon_schedule(self, page: Page) -> bool:
        # Douyin blocks scheduled publish when it is under 2h.
        warn_markers = [
            "\u8ddd\u79bb\u5b9a\u65f6\u53d1\u5e03\u65f6\u95f4\u5c0f\u4e8e2\u5c0f\u65f6",
            "\u8bf7\u91cd\u65b0\u8bbe\u7f6e\u53d1\u5e03\u65f6\u95f4",
        ]
        warn_visible = False
        for marker in warn_markers:
            if await self._is_text_visible(page, marker):
                warn_visible = True
                break
        if not warn_visible:
            return False

        new_publish_time = datetime.now() + timedelta(minutes=170)
        douyin_logger.warning(
            f"  [-] schedule too close, auto-adjust to {new_publish_time.strftime('%Y-%m-%d %H:%M')}"
        )
        await self.set_schedule_time_douyin(page, new_publish_time)
        await asyncio.sleep(0.8)
        return True

    async def handle_upload_error(self, page):
        douyin_logger.info('视频出错了，重新上传中')
        upload_input = await self._locate_upload_input(page)
        if upload_input is None:
            raise RuntimeError("Upload input not found while retrying. Cookie may be invalid; please login again.")
        await upload_input.set_input_files(self.file_path)

    async def _locate_upload_input(self, page: Page):
        # The old selector ("div[class^='container'] input") can match login inputs.
        # Prefer dedicated file inputs.
        candidates = [
            "div.progress-div input[type='file']",
            "div[class^='semi-upload'] input[type='file']",
            "input[type='file']",
        ]
        for selector in candidates:
            loc = page.locator(selector).first
            try:
                await loc.wait_for(timeout=5000)
                return loc
            except:
                continue
        return None

    async def upload(self, playwright: Playwright) -> None:
        # 使用 Chromium 浏览器启动一个浏览器实例
        if self.local_executable_path:
            browser = await playwright.chromium.launch(headless=self.headless, executable_path=self.local_executable_path)
        else:
            browser = await playwright.chromium.launch(headless=self.headless)
        # 创建一个浏览器上下文，使用指定的 cookie 文件
        context = await browser.new_context(storage_state=f"{self.account_file}")
        context = await set_init_script(context)
        try:
            await context.set_geolocation({"latitude": 39.9042, "longitude": 116.4074, "accuracy": 50})
            await context.grant_permissions(["geolocation"], origin="https://creator.douyin.com")
        except Exception:
            pass

        # 创建一个新的页面
        page = await context.new_page()
        # 访问指定的 URL
        await page.goto("https://creator.douyin.com/creator-micro/content/upload")
        douyin_logger.info(f'[+]正在上传-------{self.title}.mp4')
        # 等待页面跳转到指定的 URL，没进入，则自动等待到超时
        douyin_logger.info(f'[-] 正在打开主页...')
        await page.wait_for_url("https://creator.douyin.com/creator-micro/content/upload")
        # 点击 "上传视频" 按钮
        upload_input = await self._locate_upload_input(page)
        if upload_input is None:
            raise RuntimeError("Upload input not found. Cookie may be invalid; please login again.")
        await upload_input.set_input_files(self.file_path)

        # 等待页面跳转到指定的 URL 2025.01.08修改在原有基础上兼容两种页面
        while True:
            try:
                # 尝试等待第一个 URL
                await page.wait_for_url(
                    "https://creator.douyin.com/creator-micro/content/publish?enter_from=publish_page", timeout=3000)
                douyin_logger.info("[+] 成功进入version_1发布页面!")
                break  # 成功进入页面后跳出循环
            except Exception:
                try:
                    # 如果第一个 URL 超时，再尝试等待第二个 URL
                    await page.wait_for_url(
                        "https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page",
                        timeout=3000)
                    douyin_logger.info("[+] 成功进入version_2发布页面!")

                    break  # 成功进入页面后跳出循环
                except:
                    print("  [-] 超时未进入视频发布页面，重新尝试...")
                    await asyncio.sleep(0.5)  # 等待 0.5 秒后重新尝试
        # 填充标题和话题
        # 检查是否存在包含输入框的元素
        # 这里为了避免页面变化，故使用相对位置定位：作品标题父级右侧第一个元素的input子元素
        await asyncio.sleep(1)
        douyin_logger.info(f'  [-] 正在填充标题和话题...')
        title_container = page.get_by_text('作品标题').locator("..").locator("xpath=following-sibling::div[1]").locator("input")
        if await title_container.count():
            await title_container.fill(self.title[:30])
        else:
            titlecontainer = page.locator(".notranslate")
            await titlecontainer.click()
            await page.keyboard.press("Backspace")
            await page.keyboard.press("Control+KeyA")
            await page.keyboard.press("Delete")
            await page.keyboard.type(self.title)
            await page.keyboard.press("Enter")
        css_selector = ".zone-container"
        for index, tag in enumerate(self.tags, start=1):
            await page.type(css_selector, "#" + tag)
            await page.press(css_selector, "Space")
        douyin_logger.info(f'总共添加{len(self.tags)}个话题')
        while True:
            # 判断重新上传按钮是否存在，如果不存在，代表视频正在上传，则等待
            try:
                #  新版：定位重新上传
                number = await page.locator('[class^="long-card"] div:has-text("重新上传")').count()
                if number > 0:
                    douyin_logger.success("  [-]视频上传完毕")
                    break
                else:
                    douyin_logger.info("  [-] 正在上传视频中...")
                    await asyncio.sleep(2)

                    if await page.locator('div.progress-div > div:has-text("上传失败")').count():
                        douyin_logger.error("  [-] 发现上传出错了... 准备重试")
                        await self.handle_upload_error(page)
            except:
                douyin_logger.info("  [-] 正在上传视频中...")
                await asyncio.sleep(2)

        if self.productLink and self.productTitle:
            douyin_logger.info(f'  [-] 正在设置商品链接...')
            await self.set_product_link(page, self.productLink, self.productTitle)
            douyin_logger.info(f'  [+] 完成设置商品链接...')
        
        #上传视频封面
        await self.set_thumbnail(page, self.thumbnail_path)

        # 更换可见元素
        await self.set_location(page, "")


        # 頭條/西瓜
        third_part_element = '[class^="info"] > [class^="first-part"] div div.semi-switch'
        # 定位是否有第三方平台
        if await page.locator(third_part_element).count():
            # 检测是否是已选中状态
            if 'semi-switch-checked' not in await page.eval_on_selector(third_part_element, 'div => div.className'):
                await page.locator(third_part_element).locator('input.semi-switch-native-control').click()

        if self.publish_date != 0:
            await self.set_schedule_time_douyin(page, self.publish_date)

        # 判断视频是否发布成功
        max_publish_checks = 120
        for attempt in range(1, max_publish_checks + 1):
            try:
                await self.clear_cover_overlays(page)
                await self._fix_too_soon_schedule(page)

                publish_button = page.get_by_role('button', name="发布", exact=True).first
                if await publish_button.count() and await publish_button.is_visible():
                    await publish_button.click(timeout=3000)

                await asyncio.sleep(0.8)
                if await self._fix_too_soon_schedule(page):
                    continue

                if await self._publish_success_detected(page):
                    douyin_logger.success("  [-]视频发布成功")
                    break

                await page.wait_for_url("**/creator-micro/content/manage**", timeout=2500)
                douyin_logger.success("  [-]视频发布成功")
                break
            except Exception as e:
                # 尝试处理封面问题
                await self.handle_auto_video_cover(page)
                await self._fix_too_soon_schedule(page)
                if await self._publish_success_detected(page):
                    douyin_logger.success("  [-]视频发布成功")
                    break
                if attempt % 10 == 0:
                    douyin_logger.warning(
                        f"  [-] waiting publish result... attempt={attempt} url={page.url} err={e}"
                    )
                await asyncio.sleep(0.8)
        else:
            raise RuntimeError(
                f"Publish did not complete after {max_publish_checks} checks. Current URL: {page.url}"
            )

        await context.storage_state(path=self.account_file)  # 保存cookie
        douyin_logger.success('  [-]cookie更新完毕！')
        await asyncio.sleep(2)  # 这里延迟是为了方便眼睛直观的观看
        # 关闭浏览器上下文和浏览器实例
        await context.close()
        await browser.close()

    async def handle_auto_video_cover(self, page):
        """
        处理必须设置封面的情况，点击推荐封面的第一个
        """
        # 1. 判断是否出现 "请设置封面后再发布" 的提示
        # 必须确保提示是可见的 (is_visible)，因为 DOM 中可能存在隐藏的历史提示
        if await page.get_by_text("请设置封面后再发布").first.is_visible():
            print("  [-] 检测到需要设置封面提示...")

            # 2. 定位“智能推荐封面”区域下的第一个封面
            # 使用 class^= 前缀匹配，避免 hash 变化导致失效
            recommend_cover = page.locator('[class^="recommendCover-"]').first

            if await recommend_cover.count():
                print("  [-] 正在选择第一个推荐封面...")
                try:
                    await recommend_cover.click()
                    await asyncio.sleep(1)  # 等待选中生效

                    # 3. 处理可能的确认弹窗 "是否确认应用此封面？"
                    # 并不一定每次都会出现，健壮性判断：如果出现弹窗，则点击确定
                    confirm_text = "是否确认应用此封面？"
                    if await page.get_by_text(confirm_text).first.is_visible():
                        print(f"  [-] 检测到确认弹窗: {confirm_text}")
                        # 直接点击“确定”按钮，不依赖脆弱的 CSS 类名
                        await page.get_by_role("button", name="确定").click()
                        print("  [-] 已点击确认应用封面")
                        await asyncio.sleep(1)

                    print("  [-] 已完成封面选择流程")
                    return True
                except Exception as e:
                    print(f"  [-] 选择封面失败: {e}")

        return False

    async def set_thumbnail(self, page: Page, thumbnail_path: str):
        if not thumbnail_path:
            return

        choose_cover_text = "\u9009\u62e9\u5c01\u9762"
        set_vertical_text = "\u8bbe\u7f6e\u7ad6\u5c01\u9762"
        set_horizontal_text = "\u8bbe\u7f6e\u6a2a\u5c01\u9762"
        finish_text = "\u5b8c\u6210"

        douyin_logger.info("  [-] setting video cover...")
        await page.click(f'text="{choose_cover_text}"')
        await page.wait_for_selector("div.dy-creator-content-modal", timeout=10000)

        # Keep original flow: select vertical cover and upload thumbnail.
        vertical_tab = page.get_by_text(set_vertical_text).first
        try:
            if await vertical_tab.count() and await vertical_tab.is_visible():
                await vertical_tab.click(force=True, timeout=2000)
        except Exception:
            pass

        await page.wait_for_timeout(1200)
        upload_input = page.locator("div[class^='semi-upload upload'] >> input.semi-upload-hidden-input").first
        if await upload_input.count():
            await upload_input.set_input_files(thumbnail_path)
            await page.wait_for_timeout(1200)

        # Original complete click.
        try:
            await page.locator(f"div#tooltip-container button:visible:has-text('{finish_text}')").first.click(timeout=1800)
        except Exception:
            pass

        modal = page.locator("div.dy-creator-content-modal").first

        # Targeted fix only: if still blocked, click the "完成" button left to "设置横封面".
        try:
            if await modal.count() and await modal.is_visible():
                horizontal_btn = page.locator(
                    f"div.dy-creator-content-modal button:has-text('{set_horizontal_text}')"
                ).first
                clicked_done_left = False
                if await horizontal_btn.count() and await horizontal_btn.is_visible():
                    done_left_btn = horizontal_btn.locator("xpath=preceding-sibling::button[1]")
                    if await done_left_btn.count() and await done_left_btn.is_visible():
                        done_left_text = await done_left_btn.text_content() or ""
                        if finish_text in done_left_text:
                            await done_left_btn.click(force=True, timeout=2000)
                            clicked_done_left = True

                if not clicked_done_left:
                    complete_btn = page.locator(
                        f"div.dy-creator-content-modal button:has-text('{finish_text}')"
                    ).first
                    if await complete_btn.count() and await complete_btn.is_visible():
                        await complete_btn.click(force=True, timeout=2000)
        except Exception:
            pass

        # Wait modal close; if not closed, click close button as fallback.
        try:
            await page.wait_for_selector("div.dy-creator-content-modal", state='hidden', timeout=5000)
        except Exception:
            close_selectors = [
                "div.dy-creator-content-modal [class*='close']",
                "div.dy-creator-content-modal .semi-modal-close",
            ]
            for selector in close_selectors:
                target = page.locator(selector).first
                try:
                    if await target.count() and await target.is_visible():
                        await target.click(force=True, timeout=1200)
                        break
                except Exception:
                    continue

        await self.clear_cover_overlays(page)
        douyin_logger.info("  [+] video cover step completed")

    async def set_location(self, page: Page, location: str = ""):
        if not location:
            return
        # todo supoort location later
        # await page.get_by_text('添加标签').locator("..").locator("..").locator("xpath=following-sibling::div").locator(
        #     "div.semi-select-single").nth(0).click()
        await page.locator('div.semi-select span:has-text("输入地理位置")').click()
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(2000)
        await page.keyboard.type(location)
        await page.wait_for_selector('div[role="listbox"] [role="option"]', timeout=5000)
        await page.locator('div[role="listbox"] [role="option"]').first.click()

    async def handle_product_dialog(self, page: Page, product_title: str):
        """处理商品编辑弹窗"""

        await page.wait_for_timeout(2000)
        await page.wait_for_selector('input[placeholder="请输入商品短标题"]', timeout=10000)
        short_title_input = page.locator('input[placeholder="请输入商品短标题"]')
        if not await short_title_input.count():
            douyin_logger.error("[-] 未找到商品短标题输入框")
            return False
        product_title = product_title[:10]
        await short_title_input.fill(product_title)
        # 等待一下让界面响应
        await page.wait_for_timeout(1000)

        finish_button = page.locator('button:has-text("完成编辑")')
        if 'disabled' not in await finish_button.get_attribute('class'):
            await finish_button.click()
            douyin_logger.debug("[+] 成功点击'完成编辑'按钮")
            
            # 等待对话框关闭
            await page.wait_for_selector('.semi-modal-content', state='hidden', timeout=5000)
            return True
        else:
            douyin_logger.error("[-] '完成编辑'按钮处于禁用状态，尝试直接关闭对话框")
            # 如果按钮禁用，尝试点击取消或关闭按钮
            cancel_button = page.locator('button:has-text("取消")')
            if await cancel_button.count():
                await cancel_button.click()
            else:
                # 点击右上角的关闭按钮
                close_button = page.locator('.semi-modal-close')
                await close_button.click()
            
            await page.wait_for_selector('.semi-modal-content', state='hidden', timeout=5000)
            return False
        
    async def set_product_link(self, page: Page, product_link: str, product_title: str):
        """设置商品链接功能"""
        await page.wait_for_timeout(2000)  # 等待2秒
        try:
            # 定位"添加标签"文本，然后向上导航到容器，再找到下拉框
            await page.wait_for_selector('text=添加标签', timeout=10000)
            dropdown = page.get_by_text('添加标签').locator("..").locator("..").locator("..").locator(".semi-select").first
            if not await dropdown.count():
                douyin_logger.error("[-] 未找到标签下拉框")
                return False
            douyin_logger.debug("[-] 找到标签下拉框，准备选择'购物车'")
            await dropdown.click()
            ## 等待下拉选项出现
            await page.wait_for_selector('[role="listbox"]', timeout=5000)
            ## 选择"购物车"选项
            await page.locator('[role="option"]:has-text("购物车")').click()
            douyin_logger.debug("[+] 成功选择'购物车'")
            
            # 输入商品链接
            ## 等待商品链接输入框出现
            await page.wait_for_selector('input[placeholder="粘贴商品链接"]', timeout=5000)
            # 输入
            input_field = page.locator('input[placeholder="粘贴商品链接"]')
            await input_field.fill(product_link)
            douyin_logger.debug(f"[+] 已输入商品链接: {product_link}")
            
            # 点击"添加链接"按钮
            add_button = page.locator('span:has-text("添加链接")')
            ## 检查按钮是否可用（没有disable类）
            button_class = await add_button.get_attribute('class')
            if 'disable' in button_class:
                douyin_logger.error("[-] '添加链接'按钮不可用")
                return False
            await add_button.click()
            douyin_logger.debug("[+] 成功点击'添加链接'按钮")
            ## 如果链接不可用
            await page.wait_for_timeout(2000)
            error_modal = page.locator('text=未搜索到对应商品')
            if await error_modal.count():
                confirm_button = page.locator('button:has-text("确定")')
                await confirm_button.click()
                # await page.wait_for_selector('.semi-modal-content', state='hidden', timeout=5000)
                douyin_logger.error("[-] 商品链接无效")
                return False

            # 填写商品短标题
            if not await self.handle_product_dialog(page, product_title):
                return False
            
            # 等待链接添加完成
            douyin_logger.debug("[+] 成功设置商品链接")
            return True
        except Exception as e:
            douyin_logger.error(f"[-] 设置商品链接时出错: {str(e)}")
            return False

    async def clear_cover_overlays(self, page: Page) -> None:
        portal_selector = "div.dy-creator-content-portal"
        try:
            if await page.locator(portal_selector).count() == 0:
                return
        except Exception:
            return

        for _ in range(2):
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await page.wait_for_timeout(250)
            try:
                if await page.locator(f"{portal_selector}:visible").count() == 0:
                    return
            except Exception:
                pass
        selectors = [
            f"{portal_selector} button:has-text('\u53d6\u6d88')",
            f"{portal_selector} button:has-text('\u5173\u95ed')",
            f"{portal_selector} button:has-text('\u5b8c\u6210')",
            f"{portal_selector} [aria-label='\u5173\u95ed']",
            f"{portal_selector} [class*='close']",
            f"{portal_selector} [class*='Close']",
            f"{portal_selector} [class*='cancel']",
            f"{portal_selector} [class*='Cancel']",
        ]
        for selector in selectors:
            target = page.locator(selector).first
            try:
                if await target.count() and await target.is_visible():
                    await target.click(force=True, timeout=1200)
                    await page.wait_for_timeout(250)
                    if await page.locator(f"{portal_selector}:visible").count() == 0:
                        return
            except Exception:
                continue

        try:
            await page.evaluate(
                """() => {
                    for (const root of document.querySelectorAll('div.dy-creator-content-portal')) {
                        root.style.pointerEvents = 'none';
                        for (const el of root.querySelectorAll('*')) {
                            el.style.pointerEvents = 'none';
                        }
                    }
                }"""
            )
            douyin_logger.info("  [-] Cover popup still visible, pointer-events disabled")
        except Exception:
            pass

    async def main(self):
        async with async_playwright() as playwright:
            await self.upload(playwright)


