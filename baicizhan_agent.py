import requests
import json
import subprocess
import time
import xml.etree.ElementTree as ET

ADB_PATH = "scrcpy/adb.exe"  # ADB可执行文件路径。注：这个api-key在演示结束后就失效了
API_KEY = "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"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "bytedance-seed/seed-1.6-flash"

def get_screen_info():
    """快速获取屏幕信息：通过管道合并指令，减少 30%-50% 的耗时"""
    print("正在快速获取屏幕元素...")
    # 合并指令：dump 到手机临时目录 -> cat 出来 -> 清理。
    cmd = [ADB_PATH, "shell", "uiautomator dump /data/local/tmp/v.xml > /dev/null && cat /data/local/tmp/v.xml"]
    
    try:
        # 执行并直接获取标准输出流
        result = subprocess.run(cmd, capture_output=True, check=True)
        xml_str = result.stdout.decode('utf-8', errors='ignore')
        
        # 从字符串直接解析 XML，从第一个 '<' 标签开始解析，防止 dump 提示词干扰
        root = ET.fromstring(xml_str[xml_str.find('<'):])
        
        elements = []
        for node in root.iter():
            text = node.get("text", "")
            desc = node.get("content-desc", "")
            bounds = node.get("bounds", "")
            
            # 过滤逻辑：必须有文本/描述，且有坐标
            if (text or desc) and bounds:
                # 提取中心坐标
                b = bounds.replace("][", ",").replace("[", "").replace("]", "")
                x1, y1, x2, y2 = map(int, b.split(","))
                center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
                elements.append(f"文本: '{text}', 描述: '{desc}', 中心坐标: ({center_x}, {center_y})")
        
        return "\n".join(elements) if elements else "当前屏幕没有找到带文本的可交互元素。"
    
    except Exception as e:
        print(f"获取屏幕失败: {e}")
        return "解析屏幕失败"

def tap(x, y):
    """通过ADB执行点击"""
    print(f"正在调用adb执行点击 -> 坐标: ({x}, {y})")
    subprocess.run([ADB_PATH, "shell", "input", "tap", str(x), str(y)])
    time.sleep(2)  # 给 APP 界面加载、跳转和动画留出足够的等待时间（建议2秒）

def run_agent():    
    tools =[
        {
            "type": "function",
            "function": {
                "name": "tap_screen",
                "description": "点击屏幕上的指定坐标",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"}
                    },
                    "required":["x", "y"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "task_complete",
                "description": "打卡任务已经完成时调用此函数，结束程序",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]

    system_prompt = """你是一个安卓手机自动化打卡助手，你一开始在手机主页。
你需要调用tap_screen工具，从桌面点击【百词斩】APP，进入应用后选择类似于开始打卡的按钮，然后选择正确的单词选项，每次点一下就可以切换到下一个单词。所有单词点完后，会退出到百词斩主页，调用task_complete工具完成打卡。
请根据屏幕信息中的元素坐标做出正确的决策，必须调用工具来执行动作。"""

    # 在循环外部初始化消息历史列表
    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    # 获取初始状态的屏幕信息
    initial_screen = get_screen_info()
    messages.append({
        "role": "user", 
        "content": f"当前屏幕上的可用元素如下，请分析并采取下一步操作：\n{initial_screen}"
    })

    # 回合制循环：可控制多少步之后强制结束
    for step in range(1, 1001):
        print(f"\n{'='*20} 第 {step} 回合 {'='*20}")
        
        # 打印当前发送给模型的提示词（如果是新一轮，就是我们刚追加的Tool返回或User提示词）
        last_msg = messages[-1]
        print("【当前发给Agent的最新提示词/反馈】:")
        print(last_msg["content"][:500] + "...\n(截断显示)" if len(last_msg["content"]) > 500 else last_msg["content"])
        print("-" * 50)

        payload = {
            "model": MODEL,
            "messages": messages,   # 此时 messages 包含了过去的所有对话历史
            "tools": tools,
            "tool_choice": "auto"
        }
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        
        # 询问大模型
        try:
            response = requests.post(API_URL, headers=headers, json=payload).json()
            if "error" in response:
                print(f"API返回错误: {response['error']}")
                break
            ai_msg = response["choices"][0]["message"]
        except Exception as e:
            print(f"API请求失败: {e}")
            break

        # 务必将助手的原始回复（包含其调用的工具记录）加入历史，保证上下文完整
        messages.append(ai_msg)

        # 打印助手的文字思考内容（如果有的话）
        if ai_msg.get("content"):
            print(f"【Agent 的思考与回复】:\n{ai_msg['content']}\n")

        # 判断并执行助手的行动
        if "tool_calls" in ai_msg and ai_msg["tool_calls"]:
            tool = ai_msg["tool_calls"][0]
            func_name = tool["function"]["name"]
            tool_call_id = tool["id"]  # 记录这把工具的唯一 ID
            
            if func_name == "tap_screen":
                args = json.loads(tool["function"]["arguments"])
                print(f"【Agent 决定调用工具】: tap_screen -> 目标参数: x={args['x']}, y={args['y']}")
                
                # 执行点击操作
                tap(args["x"], args["y"])
                
                # 点击完成后，获取最新的屏幕信息
                new_screen_info = get_screen_info()
                tool_result_content = f"已成功点击坐标({args['x']}, {args['y']})。当前最新的屏幕元素如下：\n{new_screen_info}"
                
                # 以 "tool" 的身份把最新屏幕数据传回给大模型，这标志着刚刚的工具调用有了“执行结果”
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": func_name,
                    "content": tool_result_content
                })
                
            elif func_name == "task_complete":
                print("【Agent 判断任务已完成】: 调用了 task_complete 工具，打卡结束！")
                break
        else:
            print("【警告】AI 没有调用任何工具，可能不知道该干什么了。")
            # 兜底：如果它忘了用工具，发一条User消息强制让它推进任务
            messages.append({
                "role": "user",
                "content": "你刚才只是回复了文本而没有调用任何工具。请务必使用 tap_screen 进行下一步操作，或者使用 task_complete 结束任务。"
            })

        # 将完整上下文快照保存到文件 (覆盖写入)
        with open("history_log.json", "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    run_agent()