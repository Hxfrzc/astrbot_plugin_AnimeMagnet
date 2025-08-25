import aiohttp
import asyncio
import os
import re
import json
from datetime import datetime
from urllib.parse import quote
from typing import Dict, Any
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
import astrbot.api.message_components as Comp
from astrbot.api.message_components import Node, Plain, Image, Nodes
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import AstrBotConfig
from astrbot.api import logger
from .method import TEMP_DIR, get_img_changeFormat

#自定义异常类
class ExceedSearchLimit(Exception):
    '''检索匹配内容超过一种'''
    pass

class FetchError(Exception):
    '''抓取错误'''
    pass

class NoAnimeFound(Exception):
    '''找不到对应的番剧'''
    pass

#user_agent
ua = UserAgent()


#蜜柑动漫磁链爬取方法
class Mikan():
    def __init__(self):
        self.base_url = 'https://mikanani.me/'
        self.header = {
            "User-Agent" : ua.random
        }
        self.html = None
        self.proxy = None
        self.title = None
        self.url = None
        self.tracker = None


    #通用请求网页
    async def fetch_url(self, url, session):
        base_url = url
        header = self.header
        try:
            async with session.get(base_url, headers=header, proxy=self.proxy) as response:
                response.raise_for_status()
                fetch_html = await response.text()
                html = BeautifulSoup(fetch_html,"html.parser")
                return html
        except Exception as e:
             raise FetchError(f'抓取{base_url}错误') from e
        
        
    #通用字幕组/标题/磁链定位
    async def sub_title_magnet(self, id):
        start_node = self.html.find('div', class_= f'subgroup-scroll-top-{id}')
        html_between = []
        try:
            for sibling in start_node.find_next_siblings():
                if f'subgroup-scroll-end-{id}' in sibling.get('class', []):
                    break
                html_between.append(sibling)
            
            return html_between

        except Exception:
            raise        

    
    #详情页面处理
    async def html_magnet(self, subid, html:BeautifulSoup):
        subname = []
        magnet = []
        output = []
        if (html[-1]).get('data-take'):
            pattern = r'(\d+)$'
            bgmid = re.search(pattern, self.url)
            more_link = f'https://mikanani.me/Home/ExpandEpisodeTable?bangumiId={bgmid.group(1)}&subtitleGroupId={subid}&take=115'
            try:
                async with aiohttp.ClientSession() as session:
                    more_mgt = await self.fetch_url(more_link, session)
            except Exception:
                raise
            subname_list = more_mgt.find_all('a',class_ = "magnet-link-wrap")
            magnet_list = more_mgt.find_all('a',class_ = "js-magnet magnet-link")

        else:
            subname_list = html[-1].find_all('a',class_ = "magnet-link-wrap")
            magnet_list = html[-1].find_all('a',class_ = "js-magnet magnet-link")
        for sn in subname_list:
            subname.append(sn.text)
        for mgt in magnet_list:
            mgt = mgt.get("data-clipboard-text")
            mgt_parts = mgt.split('&', 1)
            mgt = mgt_parts[0]
            self.tracker = f'&{mgt_parts[1]}'
            magnet.append(mgt)
        for c in range(len(subname)):
            output.append(f'{subname[c]}:\n{magnet[c]}\n')
        return output

    def re_quality(self, mgt_list):
        quality_mgt = []
        regex = r'\b1080[pP]\b'
        for mgt in mgt_list:
            if re.search(regex, mgt):
                quality_mgt.append(mgt)
        
        return quality_mgt





    #查找匹配动漫  
    async def search_anime_count(self, proxy_set, keyword, session):
        q_keyword = quote(keyword)
        base_url = f'https://mikanani.me/Home/Search?searchstr={q_keyword}'
        header = self.header
        self.proxy = proxy_set
        try:
            async with session.get(base_url, headers=header, proxy=self.proxy) as response:
                response.raise_for_status()
                res_html = await response.text()
                html = BeautifulSoup(res_html,"html.parser")
                title_list = html.find_all('div',class_ = 'an-text')

                #处理并返回检索结果
                if len(title_list) == 1:
                    self.title = (title_list[0])['title']
                    url = html.select('ul.list-inline.an-ul > li > a[target="_blank"]')
                    self.url = url[0]
                    return self.title
                
                elif len(title_list) == 0:
                    raise NoAnimeFound(f'找不到对应的番剧，请更改搜索词，缩短名称，使用全称，或者去除符号来进行搜索')
                
                else:
                    #处理类似“xxxx”和“xxxx 第x季"之间无法区分的问题
                    default = False
                    title = []
                    for l in title_list:
                        if keyword == l.text:
                            self.title = l.text
                            url = html.select('ul.list-inline.an-ul > li > a[target="_blank"]')
                            for n, t in enumerate(url):
                                if l.text == (t.find_all('div',class_ = 'an-text'))[0].text:
                                    self.url = url[n]
                                    break
                            default = True
                            return self.title

                    if default is False:
                        for l in title_list:
                            title.append(l['title'])
                            out_put = '\n'.join(title)
                        raise ExceedSearchLimit(f'找到多个番剧，请重新选择:\n{out_put}')
                    
                                            
        except Exception:
            raise

    #获取番剧封面图片
    async def get_anime_image(self):
        p_link = (self.html.find_all('div',class_ = 'bangumi-poster'))[0].get('style')
        pattern = re.compile(r"url\('(.*?)\?")
        link = (pattern.search(p_link)).group(1)
        img_link = f'https://mikanani.me{link}'

        try:
            imgpath = await get_img_changeFormat(img_link, self.proxy, TEMP_DIR)
            with open(imgpath,"rb") as img:
                img_byte = img.read()
            
            #清理缓存图片
            if os.path.exists(imgpath):
                try:
                    os.remove(imgpath)
                except Exception as e:
                    logger.error(f"删除转换后图片缓存文件失败，错误原因:{e}")

            return img_byte       
        
        except Exception:
            raise
    
    #获取番剧基础信息
    def get_anime_info(self):
        info = self.html.find_all('p',class_ = 'bangumi-info')
        bgm_info = self.html.find_all('a',class_ = 'w-other-c')
        bgm_link  = bgm_info[1].text 
        ever_data = info[0].text 
        output = f'{ever_data}\nBgm链接：{bgm_link}'

        return output

    
    #处理并整合磁链结果
    async def get_search_magnet(self):  
        self.url = (self.url)['href'] 
        base_url = f'https://mikanani.me{self.url}'

        try:
            async with aiohttp.ClientSession() as session:
                self.html:BeautifulSoup = await self.fetch_url(base_url, session)
        except Exception:
                raise        

        try:
            #获取字幕组名单
            try:
                subgroup = {}
                id_list= []
                sub = self.html.find_all('a', class_ = 'subgroup-name')
                if sub:
                    for group in sub:
                        subname = group.text
                        id = (group.get('data-anchor')).removeprefix('#')
                        id_list.append(id)
                        subgroup[id] = subname
                else:
                    raise Exception(f"字幕组名单获取失败，为空") 
            except Exception:
                raise           

            #并发执行获取任务
            tasks = []
            try:
                for id in id_list:
                    subid = id
                    task = asyncio.create_task(self.sub_title_magnet(subid))
                    tasks.append(task)
                    op_html = await asyncio.gather(*tasks)

            except Exception:
                raise

            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                logger.info(f'并发获取任务已全部完成')
            
            #处理获取磁力链结果
            results = {}
            for i, html in enumerate(op_html):
                subid = id_list[i]
                sub_name = subgroup.get(subid)
                name_mgt = await self.html_magnet(subid, html)
                quality_mgt = self.re_quality(name_mgt)
                results[sub_name] = quality_mgt
            
            return self.tracker, results
        
        except Exception:
            raise

    #整理信息输出格式
    def output_format(self):
        info = self.get_anime_info()
        output = f'{self.title}\n{info}'

        return output




@register("Animemagnet", "Hxfrzc", "这是一个动漫磁链获取插件", "1.0")
class bt_getter(Star):
    def __init__(self, context: Context, config:AstrBotConfig):
        super().__init__(context)
        self.context_config = self.context.get_config()
        self.config = config

        #获取代理配置
        self.cmd_config_path = os.path.join(self.context.get_config().get("data_dir", "data"),  f"cmd_config.json")
        with open(self.cmd_config_path, 'r', encoding='utf-8-sig') as f:
            set_proxy = (json.load(f)).get("http_proxy")

        #如果没有设置代理选择astrbot设置的全局代理
        self.proxy = self.config.get('proxy', None)
        if not self.proxy:
            self.proxy = set_proxy

        self.mk = Mikan()

    
    @filter.command("bt")
    async def bt(self, event: AstrMessageEvent):
        """
        获取指定动漫的磁力链接，
        使用方法  /bt [动漫名称]
        """ 
        word = event.message_str.split(maxsplit=1)
        if len(word)<2:
            yield event.plain_result("参数错误，请输入番剧，例子：/bt 请问你今天要来点兔子吗")
            return
        
        keyword = word[1]
        yield event.plain_result(f"正在搜索：{keyword}")

        try:
            #处理查询结果
            try:
                async with aiohttp.ClientSession() as session:
                    title = await self.mk.search_anime_count(self.proxy, keyword, session)
                    yield event.plain_result(f"匹配到动漫：{title}，正在返回磁力链")

            except NoAnimeFound as e:
                yield event.plain_result(f'{e}')

            except ExceedSearchLimit as e:
                yield event.plain_result(f"{e}")
            
            except Exception as e:
                logger.error(f'处理失败，错误原因{e}')
            
            #处理查询到的磁链
            try:
                trakcer, name_bt = await self.mk.get_search_magnet()
                subname_list = list(name_bt.keys())
                subname = '\n'.join(subname_list)

            except Exception as e:
                logger.error(f'获取磁链失败，错误原因{e}')

            info = self.mk.output_format() 
            chain_list = []
            info_chains = Node(
                uin=3974507586,
                name="玖玖瑠",
                content=[
                    Image.fromBytes(await self.mk.get_anime_image()),
                    Plain(f'{info}\n'),
                ]
            )
            tracker_chains = Node(
                uin=3974507586,
                name="玖玖瑠",
                content=[
                    Plain(f'Tracker：\n{trakcer}'),
                ]
            )
            sub_chains = Node(
                uin=3974507586,
                name="玖玖瑠",
                content=[
                    Plain(f'字幕组名单：\n{subname}'),
                ]
            )
            chain_list.append(info_chains)
            chain_list.append(sub_chains)
            chain_list.append(tracker_chains)

            for c in range(len(name_bt)):
                if not name_bt[subname_list[c]]:
                    continue

                for m in range(len(name_bt[subname_list[c]])):
                    mgt_chains = Node(
                        uin=3974507586,
                        name="玖玖瑠",
                        content=[
                            Plain(f'字幕组 → 「{subname_list[c]}」:\n🧲：\n{name_bt[subname_list[c]][m]}')
                        ]
                    )
                    chain_list.append(mgt_chains)

                if len(name_bt[subname_list[c]]) >5:
                    break

            chain_obj = Nodes(nodes=chain_list)


            yield event.chain_result([chain_obj])    
        
        except Exception as e:
            logger.error(f'出错，错误原因{e}')

        
    @filter.command("btn")
    async def btn(self, event: AstrMessageEvent):
        """
        获取指定动漫最新的磁力链接，
        使用方法  /btn [动漫名称]
        """ 
        word = event.message_str.split(maxsplit=1)
        if len(word)<2:
            yield event.plain_result("参数错误，请输入番剧，例子：/btn 请问你今天要来点兔子吗")
            return
        
        keyword = word[1]
        yield event.plain_result(f"正在搜索：{keyword}")

        try:
            #处理查询结果
            try:
                async with aiohttp.ClientSession() as session:
                    title = await self.mk.search_anime_count(self.proxy, keyword, session)
                    yield event.plain_result(f"匹配到动漫：{title}，正在返回最新获取的磁力链")

            except NoAnimeFound as e:
                yield event.plain_result(f'{e}')

            except ExceedSearchLimit as e:
                yield event.plain_result(f"{e}")
            
            except Exception as e:
                logger.error(f'处理失败，错误原因{e}')
            
            #处理查询到的磁链
            try:
                trakcer, name_bt = await self.mk.get_search_magnet()
                subname_list = list(name_bt.keys())
                subname = '\n'.join(subname_list)

            except Exception as e:
                logger.error(f'获取磁链失败，错误原因{e}')

            info = self.mk.output_format() 
            chain_list = []
            info_chains = Node(
                uin=3974507586,
                name="玖玖瑠",
                content=[
                    Image.fromBytes(await self.mk.get_anime_image()),
                    Plain(f'{info}\n'),
                ]
            )
            tracker_chains = Node(
                uin=3974507586,
                name="玖玖瑠",
                content=[
                    Plain(f'Tracker：\n{trakcer}'),
                ]
            )
            sub_chains = Node(
                uin=3974507586,
                name="玖玖瑠",
                content=[
                    Plain(f'字幕组名单：\n{subname}'),
                ]
            )
            chain_list.append(info_chains)
            chain_list.append(sub_chains)
            chain_list.append(tracker_chains)
            for m in range(len(subname_list)):
                sub_title = Node(
                uin=3974507586,
                name="玖玖瑠",
                content=[
                    Plain(f'「{subname_list[m]}」:'),
                ]
            )
                mgt_chains = Node(
                    uin=3974507586,
                    name="玖玖瑠",
                    content=[
                        Node(
                            uin=3974507586,
                            name="玖玖瑠",
                            content=[
                            Plain(f'字幕组 → 「{subname_list[m]}」:\n🧲：\n{name_bt[subname_list[m]][0]}')
                            ]
                        )
                    ]
                )
                chain_list.append(sub_title)
                chain_list.append(mgt_chains)
            chain_obj = Nodes(nodes=chain_list)


            yield event.chain_result([chain_obj]) 

        except Exception as e:
            logger.error(f'出错，错误原因{e}')       
            


    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        pass
