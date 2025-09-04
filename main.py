from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.all import *
from astrbot.core.message.components import Reply, Plain, Image
from .utils.ttp import generate_image_openrouter

import time
import threading
import asyncio
from functools import wraps
from collections import defaultdict, deque


class RateLimiter:
    """基于滑动时间窗口的频率限制器，支持并发安全和自动内存清理"""
    
    def __init__(self, max_requests=10, time_window=60, cleanup_interval=300):
        """
        初始化频率限制器
        
        Args:
            max_requests: 每个时间窗口内的最大请求次数
            time_window: 时间窗口长度（秒）
            cleanup_interval: 自动清理间隔（秒）
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.cleanup_interval = cleanup_interval
        
        # 使用字典存储每个群组的请求时间戳队列
        self.request_records = defaultdict(deque)
        
        # 线程锁，确保并发安全
        self.lock = threading.RLock()
        
        # 上次清理时间
        self.last_cleanup_time = time.time()
    
    def _cleanup_expired_records(self):
        """清理过期的请求记录，防止内存泄漏"""
        current_time = time.time()
        
        # 如果距离上次清理时间未超过清理间隔，则跳过
        if current_time - self.last_cleanup_time < self.cleanup_interval:
            return
        
        with self.lock:
            # 遍历所有群组的记录
            groups_to_remove = []
            for group_id, timestamps in self.request_records.items():
                # 移除过期的时间戳
                while timestamps and current_time - timestamps[0] > self.time_window:
                    timestamps.popleft()
                
                # 如果队列为空，标记该群组记录可以删除
                if not timestamps:
                    groups_to_remove.append(group_id)
            
            # 删除空的群组记录
            for group_id in groups_to_remove:
                del self.request_records[group_id]
            
            # 更新上次清理时间
            self.last_cleanup_time = current_time
            
            if groups_to_remove:
                logger.debug(f"RateLimiter清理了 {len(groups_to_remove)} 个空的群组记录")
    
    def check_rate_limit(self, group_id):
        """
        检查指定群组是否超过频率限制
        
        Args:
            group_id: 群组ID（私聊使用用户ID）
            
        Returns:
            tuple: (is_allowed, remaining_time)
                - is_allowed: 是否允许请求
                - remaining_time: 剩余冷却时间（秒），如果允许请求则为0
        """
        current_time = time.time()
        
        # 定期清理过期记录
        self._cleanup_expired_records()
        
        with self.lock:
            timestamps = self.request_records[group_id]
            
            # 移除过期的时间戳
            while timestamps and current_time - timestamps[0] > self.time_window:
                timestamps.popleft()
            
            # 检查是否超过限制
            if len(timestamps) >= self.max_requests:
                # 计算剩余冷却时间
                oldest_timestamp = timestamps[0]
                remaining_time = int(self.time_window - (current_time - oldest_timestamp)) + 1
                return False, remaining_time
            
            # 记录当前请求时间戳
            timestamps.append(current_time)
            return True, 0
    
    def update_config(self, max_requests=None, time_window=None, cleanup_interval=None):
        """动态更新配置"""
        with self.lock:
            if max_requests is not None:
                self.max_requests = max_requests
            if time_window is not None:
                self.time_window = time_window
            if cleanup_interval is not None:
                self.cleanup_interval = cleanup_interval
            
            logger.info(f"RateLimiter配置已更新: max_requests={self.max_requests}, "
                       f"time_window={self.time_window}, cleanup_interval={self.cleanup_interval}")


def rate_limited(rate_limiter_attr='rate_limiter', config_attr='config'):
    """
    频率限制装饰器，支持异步生成器函数
    
    Args:
        rate_limiter_attr: RateLimiter实例的属性名
        config_attr: 配置字典的属性名
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, event: AstrMessageEvent):
            # 检查是否启用频率限制
            config = getattr(self, config_attr, {})
            if not config.get('rate_limit_enabled', True):
                # 未启用频率限制，直接执行原函数
                async for result in func(self, event):
                    yield result
                return
            
            # 获取RateLimiter实例
            rate_limiter = getattr(self, rate_limiter_attr, None)
            if rate_limiter is None:
                logger.warning("RateLimiter实例未初始化，跳过频率限制检查")
                async for result in func(self, event):
                    yield result
                return
            
            # 确定群组ID（私聊使用用户ID）
            try:
                if hasattr(event, 'group_id') and event.group_id:
                    group_id = f"group_{event.group_id}"
                elif hasattr(event, 'user_id') and event.user_id:
                    group_id = f"user_{event.user_id}"
                else:
                    # 无法确定群组ID，记录警告但不阻止请求
                    logger.warning("无法确定群组ID或用户ID，跳过频率限制检查")
                    async for result in func(self, event):
                        yield result
                    return
            except Exception as e:
                logger.error(f"获取群组ID时出错: {e}，跳过频率限制检查")
                async for result in func(self, event):
                    yield result
                return
            
            # 执行频率限制检查
            try:
                is_allowed, remaining_time = rate_limiter.check_rate_limit(group_id)
                
                if not is_allowed:
                    # 超过频率限制，返回提示消息
                    message_template = config.get('rate_limit_message',
                        "⚠️ 图片生成频率限制：当前群组已达到限制（{max_requests}次/{time_window}秒）。请在 {remaining_time} 秒后再试。")
                    
                    error_message = message_template.format(
                        max_requests=rate_limiter.max_requests,
                        time_window=rate_limiter.time_window,
                        remaining_time=remaining_time
                    )
                    
                    logger.info(f"群组 {group_id} 触发频率限制，剩余冷却时间: {remaining_time}秒")
                    yield event.chain_result([Plain(error_message)])
                    return
                
                # 未超过限制，执行原函数
                logger.debug(f"群组 {group_id} 频率限制检查通过")
                async for result in func(self, event):
                    yield result
                    
            except Exception as e:
                # 频率限制器出错，记录错误但不阻止请求（异常安全降级）
                logger.error(f"频率限制检查时出错: {e}，允许请求继续")
                async for result in func(self, event):
                    yield result
        
        return wrapper
    return decorator

@register("gemini-25-image-openrouter", "喵喵", "使用openrouter的免费api生成图片", "1.6")
class MyPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        
        # 保存完整配置，供装饰器使用
        self.config = config
        
        # 支持多个API密钥
        self.openrouter_api_keys = config.get("openrouter_api_keys", [])
        # 向后兼容：如果还在使用旧的单个API密钥配置
        old_api_key = config.get("openrouter_api_key")
        if old_api_key and not self.openrouter_api_keys:
            self.openrouter_api_keys = [old_api_key]
        
        # 自定义API base支持
        self.custom_api_base = config.get("custom_api_base", "").strip()
        
        # 模型配置
        self.model_name = config.get("model_name", "google/gemini-2.5-flash-image-preview:free").strip()
        
        # 重试配置
        self.max_retry_attempts = config.get("max_retry_attempts", 3)
        
        self.nap_server_address = config.get("nap_server_address")
        self.nap_server_port = config.get("nap_server_port")
        
        # 初始化频率限制器
        self.rate_limiter = None
        try:
            if config.get("rate_limit_enabled", True):
                max_requests = config.get("rate_limit_max_requests", 10)
                time_window = config.get("rate_limit_time_window", 60)
                cleanup_interval = config.get("rate_limit_cleanup_interval", 300)
                
                self.rate_limiter = RateLimiter(
                    max_requests=max_requests,
                    time_window=time_window,
                    cleanup_interval=cleanup_interval
                )
                logger.info(f"频率限制器已初始化: {max_requests}次/{time_window}秒")
            else:
                logger.info("频率限制功能已禁用")
        except Exception as e:
            logger.error(f"初始化频率限制器失败: {e}")
            self.rate_limiter = None

    @filter.command_group("aiimg", alias=["aiimg"])
    async def aiimg_group(self, event: AstrMessageEvent):
        """AI图像生成命令组"""
        # 显示帮助信息
        help_text = """🎨 AI图像生成命令组

可用命令：
• `/aiimg` - 显示此帮助信息
• `/aiimg生成 [描述]` - 普通图像生成
• `/aiimg手办化` - 手办风格转换（需要参考图片）
• `/aiimg帮助` - 显示帮助信息

示例：
• `/aiimg生成 一只可爱的小猫`
• `/aiimg手办化`（需要先发送图片）"""
        
        yield event.chain_result([Plain(help_text)])

    @filter.command("aiimg帮助")
    async def aiimg_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """🎨 AI图像生成命令组

可用命令：
• `/aiimg` - 显示此帮助信息
• `/aiimg生成 [描述]` - 普通图像生成
• `/aiimg手办化` - 手办风格转换（需要参考图片）
• `/aiimg帮助` - 显示帮助信息

示例：
• `/aiimg生成 一只可爱的小猫`
• `/aiimg手办化`（需要先发送图片）

提示：
- 普通图像生成：提供描述即可生成图片
- 手办风格转换：需要先发送一张图片作为参考"""
        
        yield event.chain_result([Plain(help_text)])

    @rate_limited()
    @filter.command("aiimg生成", alias=["aiimg"])
    async def aiimg_generate(self, event: AstrMessageEvent):
        """生成图像或根据参考图片修改图像"""
        # 普通模式：纯文本生成图像
        message_text = event.message_str.strip()
        image_description = message_text.replace('/aiimg生成', '', 1).strip()
        image_description = image_description.replace('/aiimg', '', 1).strip()
        
        # 注释掉提示词净化功能
        # image_description = self.sanitize_prompt(image_description)
        
        openrouter_api_keys = self.openrouter_api_keys
        nap_server_address = self.nap_server_address
        nap_server_port = self.nap_server_port

        # 根据参数决定是否使用参考图片
        input_images = []
        use_reference_images = True  # Command mode always tries to use reference images
        
        if use_reference_images:
            # 从当前对话上下文中获取图片信息
            if hasattr(event, 'message_obj') and event.message_obj and hasattr(event.message_obj, 'message'):
                for comp in event.message_obj.message:
                    if isinstance(comp, Image):
                        try:
                            base64_data = await comp.convert_to_base64()
                            input_images.append(base64_data)
                        except (IOError, ValueError, OSError) as e:
                            logger.warning(f"转换当前消息中的参考图片到base64失败: {e}")
                        except Exception as e:
                            logger.error(f"处理当前消息中的图片时出现未预期的错误: {e}")
                    elif isinstance(comp, Reply):
                        # 修复引用消息中的图片获取逻辑
                        # Reply组件的chain字段包含被引用的消息内容
                        if comp.chain:
                            for reply_comp in comp.chain:
                                if isinstance(reply_comp, Image):
                                    try:
                                        base64_data = await reply_comp.convert_to_base64()
                                        input_images.append(base64_data)
                                        logger.info(f"从引用消息中获取到图片")
                                    except (IOError, ValueError, OSError) as e:
                                        logger.warning(f"转换引用消息中的参考图片到base64失败: {e}")
                                    except Exception as e:
                                        logger.error(f"处理引用消息中的图片时出现未预期的错误: {e}")
                        else:
                            logger.debug("引用消息的chain为空，无法获取图片内容")
            
            # 记录使用的图片数量
            if input_images:
                logger.info(f"使用了 {len(input_images)} 张参考图片进行图像生成")
            else:
                logger.info("未找到参考图片，执行纯文本图像生成")

        # 调用生成图像的函数
        try:
            image_url, image_path = await generate_image_openrouter(
                image_description,
                openrouter_api_keys,
                model=self.model_name,
                input_images=input_images,
                api_base=self.custom_api_base if self.custom_api_base else None,
                max_retry_attempts=self.max_retry_attempts
            )
            
            if not image_url or not image_path:
                # 生成失败，发送错误消息
                error_chain = [Plain("图像生成失败，请检查API配置和网络连接。")]
                yield event.chain_result(error_chain)
                return
            
            # 使用 AstrBot 的标准方法返回图片
            yield event.image_result(image_path)
                
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"网络连接错误导致图像生成失败: {e}")
            error_chain = [Plain(f"网络连接错误，图像生成失败: {str(e)}")]
            yield event.chain_result(error_chain)
            return
        except ValueError as e:
            error_msg = str(e)
            if "内容过滤器阻止了图像生成" in error_msg:
                logger.error(f"内容过滤错误: {error_msg}")
                error_chain = [Plain("⚠️ 内容安全提醒：当前请求因安全限制被阻止。建议：\n1. 尝试更换描述用词\n2. 使用不同的参考图片\n3. 避免敏感内容")]
            else:
                logger.error(f"参数错误导致图像生成失败: {e}")
                error_chain = [Plain(f"参数错误，图像生成失败: {error_msg}")]
            yield event.chain_result(error_chain)
            return
        except Exception as e:
            logger.error(f"图像生成过程出现未预期的错误: {e}")
            error_chain = [Plain(f"图像生成失败: {str(e)}")]
            yield event.chain_result(error_chain)
            return

    @rate_limited()
    @filter.command("aiimg手办化")
    async def aiimg_figure(self, event: AstrMessageEvent):
        """将图片转换为收藏模型"""
        message_text = event.message_str.strip()
        # 提取用户额外的描述
        user_description = message_text.replace('/aiimg手办化', '', 1).strip()
        
        # 恢复原始的专业手办化提示词
        professional_figure_prompt = """将画面中的角色重塑为顶级收藏级树脂手办，全身动态姿势，置于角色主题底座；高精度材质，手工涂装，肌肤纹理与服装材质真实分明。
戏剧性硬光为主光源，凸显立体感，无过曝；强效补光消除死黑，细节完整可见。背景为窗边景深模糊，侧后方隐约可见产品包装盒。
博物馆级摄影质感，全身细节无损，面部结构精准。禁止：任何2D元素或照搬原图、塑料感、面部模糊、五官错位、细节丢失。"""
        
        # 如果用户提供了额外的描述，追加到手办化提示词后面
        if user_description:
            image_description = professional_figure_prompt + "\n\n用户额外要求：" + user_description
        else:
            image_description = professional_figure_prompt
        
        # 注释掉提示词净化功能
        # image_description = self.sanitize_prompt(image_description)
        
        openrouter_api_keys = self.openrouter_api_keys
        nap_server_address = self.nap_server_address
        nap_server_port = self.nap_server_port

        # 手办化模式必须使用参考图片
        input_images = []
        use_reference_images = True
            
        if use_reference_images:
            # 从当前对话上下文中获取图片信息
            if hasattr(event, 'message_obj') and event.message_obj and hasattr(event.message_obj, 'message'):
                for comp in event.message_obj.message:
                    if isinstance(comp, Image):
                        try:
                            base64_data = await comp.convert_to_base64()
                            input_images.append(base64_data)
                        except (IOError, ValueError, OSError) as e:
                            logger.warning(f"转换当前消息中的参考图片到base64失败: {e}")
                        except Exception as e:
                            logger.error(f"处理当前消息中的图片时出现未预期的错误: {e}")
                    elif isinstance(comp, Reply):
                        # 修复引用消息中的图片获取逻辑
                        # Reply组件的chain字段包含被引用的消息内容
                        if comp.chain:
                            for reply_comp in comp.chain:
                                if isinstance(reply_comp, Image):
                                    try:
                                        base64_data = await reply_comp.convert_to_base64()
                                        input_images.append(base64_data)
                                        logger.info(f"从引用消息中获取到图片")
                                    except (IOError, ValueError, OSError) as e:
                                        logger.warning(f"转换引用消息中的参考图片到base64失败: {e}")
                                    except Exception as e:
                                        logger.error(f"处理引用消息中的图片时出现未预期的错误: {e}")
                        else:
                            logger.debug("引用消息的chain为空，无法获取图片内容")
            
            # 记录使用的图片数量
            if input_images:
                logger.info(f"使用了 {len(input_images)} 张参考图片进行图像生成")
            else:
                # 手办化模式必须包含参考图片
                error_chain = [Plain("手办化模式必须包含参考图片，请先发送图片再使用 `/aiimg手办化` 命令")]
                yield event.chain_result(error_chain)
                return

        # 调用生成图像的函数
        try:
            image_url, image_path = await generate_image_openrouter(
                image_description,
                openrouter_api_keys,
                input_images=input_images,
                api_base=self.custom_api_base if self.custom_api_base else None
            )
            
            if not image_url or not image_path:
                # 生成失败，发送错误消息
                error_chain = [Plain("图像生成失败，请检查API配置和网络连接。")]
                yield event.chain_result(error_chain)
                return
            
            # 使用 AstrBot 的标准方法返回图片
            yield event.image_result(image_path)
                
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"网络连接错误导致图像生成失败: {e}")
            error_chain = [Plain(f"网络连接错误，图像生成失败: {str(e)}")]
            yield event.chain_result(error_chain)
            return
        except ValueError as e:
            error_msg = str(e)
            if "内容过滤器阻止了图像生成" in error_msg:
                logger.error(f"内容过滤错误: {error_msg}")
                error_chain = [Plain("⚠️ 内容安全提醒：当前请求因安全限制被阻止。建议：\n1. 尝试更换描述用词\n2. 使用不同的参考图片\n3. 避免敏感内容")]
            else:
                logger.error(f"参数错误导致图像生成失败: {e}")
                error_chain = [Plain(f"参数错误，图像生成失败: {error_msg}")]
            yield event.chain_result(error_chain)
            return
        except Exception as e:
            logger.error(f"图像生成过程出现未预期的错误: {e}")
            error_chain = [Plain(f"图像生成失败: {str(e)}")]
            yield event.chain_result(error_chain)
            return
