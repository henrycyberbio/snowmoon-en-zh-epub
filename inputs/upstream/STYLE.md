# Snowmoon 中文全译工作规范

用户要求：严格专业地翻译全书。底本为作者官网32章，GPL v3。目标语言简体中文；输出是忠实、完整、可校订的文学译稿，不是改写或节译。保留全部段落、对话、数字、条件、否定、比较、叙述视角和幽默；不要把角色观点擅自改成客观断言。中文要自然，避免机械逐词照搬，但不增添解释或心理描写。源文错误不得悄然改动事实；必要说明记入译注。注释少而必要。每章需译后逐段回读原文审校。

## 输入输出
- 输入 source/chapter-NN.json，segments 每项有 id,type,en。辅助可看 source/chapter-NN.html 与 .md。
- 输出 zh/chapter-NN.json，格式 {"chapter":N,"segments":[{"id":"NN-001","zh":"完整译文"},...],"notes":[{"id":"涉及段落id","note":"译者说明"}],"terms":{"English":"新增统一译名"},"review":{"complete":true,"checks":"具体完成的自校项目"}}。
- 每个输入id必须恰好出现一次，次序不变；不要把数段压缩成一段。不要在 JSON 里复制英文正文。整段过长可在一个zh字段用\n\n自然分段。
- 翻译所有界面、广告、书信、列表、表格文字，保留Markdown表格结构、公式、二进制/十六进制字符串、人工语言原文（读音与词例）以及图形占位 `![...](assets/...)`。图形本体由主代理保留；图中英文标签另记录到notes（准确翻译）供主代理处理。
- 非正文网站导航、亮暗按钮不翻译，已在输入中去掉。
- 段落类型break保持 `***`；章标题译成“第N章”；日期依原文为“3724年雪月3日”，不要加公元。人物内心话不要滥加原文没有的引号。对话用中文引号“”；引号内引号用‘’。
- 一般计量照原文，tick译“时刻单位”会过于冗长，统一译“刻”，milli-tick译“毫刻”；这里的刻是虚构单位不是中国古代刻。minute、hour仍为分钟、小时。不要把tick改为秒。
- 虚构语句Dzegoban拼写保留原样；对应原文英语释义译成中文。不要自行修复人工语言、字谜、数字游戏规则。专门记录歧义。
- 翻译使用你自身能力，不用外部机翻服务，也不要寻找别人已译文本替代工作。

## 基础统一术语
Snowmoon 雪月；Rainmoon 雨月（其他月份遇到时按原意译，报告新增项）
Veridia 维里迪亚；Veridian 维里迪亚人／维里迪亚的；Meldan 梅尔丹；Kalimar 卡利马尔；Elenar 艾勒纳尔
Dzego 杰戈；Dzegojan 杰戈人／杰戈的；Dzegoban 杰戈语；Pafogai Du 帕福盖都
Arctic Empire 北极帝国；Arctic Emperor 北极皇帝；Arctics 北极人（涉及政权或军队时按语境“帝国一方”）；United Cities 联合城邦
Freetown 自由城；Redshire 红郡；Devanvil 德万维尔；Northglade 北林；Northshore 北岸；Thaldur 萨尔杜尔；Greater Plum Harbor 大梅港
Gladias 格拉迪亚斯；Glad 格拉德；Seila 塞拉；Zei 泽伊；Fin 芬；Bai 拜；Mu 穆；Su 苏；Den 登
Zven 兹文；Lily 莉莉；Febric 费布里克；Hreda 赫蕾达；Vil 维尔；Daia 达娅
Deluin 德鲁因；Delwart 德尔沃特；Verdow 弗多；Lektor 莱克托；Ephelion 埃菲利昂
Minpentai 明彭台；Helisport 螺旋球运动；Kungaupei 昆高会；Bansunpei 班孙会
Emerald 翡翠（AI名称）；Silverchat 银语；Bluewhale 蓝鲸；Hydrafill 水盈；Dreadknot 恐结乐队
Order of Steering 导向会；Order 会（依语境补导向会）；Steering 导向机制／导向；Steering taxes 导向税
Acolyte 学徒；Sentinel 哨卫；Keeper 守则者；Herald 传令官；rubric 评定准则；audit 审核（不是机械译财务审计）；prediction score 预测得分
Graph Funding 图式资助；quadratic funding 二次方资助；quadratic voting 二次方投票；sortition 抽签选任（或依语境抽签）；public goods 公共物品；public aesthetics tax 公共审美税；composite land tax 综合土地税
zero-knowledge proof 零知识证明；zero-knowledge authentication 零知识认证；cryptographic attestation 密码学认证证明；homomorphic encryption 同态加密；obfuscation 混淆；hash 哈希；mixnet 混合网络；social recovery 社会恢复；API API
hand device 掌上终端；neck band 颈带；privacy robe 隐私袍；anti-transmission foil 防传输箔；autobus 自动巴士；compute box 计算盒；snow drone 雪地无人机
coin、zipcoin等币种保留原名（zipcoin不擅自音译加密品牌）；reputation 声誉；rep score 声誉分

新增专名不确定可先保留拉丁拼写并列入terms；全书最后统一，不自行强造不同译名。本表若与明确文本定义冲突请及时报告主代理。

补充统一：longhour 长时（每天10长时，每长时100分钟）；lightfoot/lightfeet 光尺。确切定义依第9章原文，不换算为现实单位。

最终月份名统一：Windmoon风月、Bloomtime花月、Grasstime草月、Harvestmoon丰收月、Firemoon火月、Fruitmoon果月、Grapetime葡萄月、Mistime雾月、Frostime霜月；均为虚构月份名。
新增：Jahn雅恩；Min敏；Utaku乌塔库；Belpaki贝尔帕基；Hapton哈普顿；Deplexi德普莱克西；Ondak翁达克；Imber因贝尔；Mogna莫格纳；Sadzu Du萨祖都；Glasscrown琉璃冠城；Tafindel塔芬德尔；Ichara伊查拉；co-governance共同治理；anti-recommended substances不建议使用的物质。
游戏术语补充：glider滑翔机；spaceship飞船；symbol符号；symbol-carrying spaceship携符飞船；intervention turn干预回合；factory工厂；rock formation岩块群；turn回合。Mov莫夫；Inglewore英格尔沃尔；nullifier作废标识符（首次附英文）。

发布版统一补充：Freetown Economics Institute 自由城经济研究所；Institute director 所长；defense company/corp 防务公司；sea/seaborne drone 海上无人艇；Group North 北方集群；Group Center 中央集群；Group South 南方集群；Group Naval 海上集群。
