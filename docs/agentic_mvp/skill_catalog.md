# MVP COC 技能目录

## 文档状态

这是目标实现期 `data/characters/agentic_mvp_skill_catalog.json` 的规范性映射说明，版本为 `coc7e-agentic-mvp-1`。它不是模组权限表，也不决定 GM 是否应该调用检定；它只回答一个机械问题：当 GM 已经选择某个 COC 能力时，Harness 从角色卡读取哪个冻结数值。

角色模板目标文件为 `data/characters/agentic_mvp_actor_templates.json`。模板必须使用下表的规范键；中文名称只用于人类阅读和 Prompt 显示，不参与运行时查找。新增键必须提升目录版本并同步更新角色模板、契约和确定性测试，不能在运行时根据中文字符串临时猜测。

## 属性键

`strength`、`constitution`、`size`、`dexterity`、`appearance`、`intelligence`、`power`、`education` 是固定的八项属性键，基础值直接来自 `ActorSheet.attributes`。

## MVP 技能键

下表覆盖当前六张角色卡和 MVP 聚焦场景需要的技能。普通技能的 `base` 是角色模板未给出覆盖值时的 COC 7e 基础值；`derived` 表示由角色卡属性确定性计算。角色模板显式覆盖值优先，但不能超过契约允许的整数范围。setting skill 仍记录目录基础值以校验版本和定义，但只有角色模板显式拥有时才进入该角色的冻结技能集合。

| 规范键 | 中文显示名 | 基础值/派生 | 用途 |
| --- | --- | --- | --- |
| `spot_hidden` | 侦查 | 25 | COC 技能 |
| `library_use` | 图书馆使用 | 20 | COC 技能 |
| `listen` | 聆听 | 20 | COC 技能 |
| `psychology` | 心理学 | 10 | COC 技能 |
| `persuade` | 说服 | 10 | COC 技能 |
| `navigate` | 导航 | 10 | COC 技能 |
| `stealth` | 潜行 | 20 | COC 技能 |
| `first_aid` | 急救 | 30 | COC 技能 |
| `fighting__brawl` | 格斗（斗殴） | 25 | 专长键 |
| `dodge` | 闪避 | `floor(dexterity / 2)` | 派生技能 |
| `charm` | 魅惑 | 15 | COC 技能 |
| `credit_rating` | 信用评级 | 0 | COC 技能 |
| `mechanical_repair` | 机械维修 | 10 | COC 技能 |
| `locksmith` | 锁匠 | 1 | COC 技能 |
| `electrical_repair` | 电气维修 | 10 | COC 技能 |
| `climb` | 攀爬 | 20 | COC 技能 |
| `swim` | 游泳 | 20 | COC 技能 |
| `occult` | 神秘学 | 5 | COC 技能 |
| `history` | 历史 | 5 | COC 技能 |
| `anthropology` | 人类学 | 1 | COC 技能 |
| `language_other__ancient_serpent` | 其他语言（古代蛇人语） | 1 | 专长键 |
| `art_craft__rigging` | 手艺（索具） | 5 | 专长键 |
| `flight` | 飞行 | 1 | 本篇角色能力，仍使用 COC 百分比检定 |

双下划线表示专长归属：`language_other__ancient_serpent` 和 `art_craft__rigging` 是两个不同的完整键，不得折叠为 `language_other` 或 `art_craft` 后让 GM 自行补全专长。`flight` 是本篇需要的 setting skill；它不扩大 Harness 的世界权限，只有实际角色卡拥有该键时才可检定。

## 目录记录的最小形状

实现期 JSON 至少保存以下字段，方便在 `session.json` 中冻结版本并让测试独立验证基础值：

```json
{
  "catalog_version": "coc7e-agentic-mvp-1",
  "skills": {
    "spot_hidden": {
      "display_name": "侦查",
      "base": {"kind": "fixed", "value": 25}
    },
    "dodge": {
      "display_name": "闪避",
      "base": {"kind": "derived", "formula": "floor(dexterity / 2)"}
    },
    "fighting__brawl": {
      "display_name": "格斗（斗殴）",
      "base": {"kind": "fixed", "value": 25},
      "specialty": "brawl"
    }
  }
}
```

装载器在初始化时解析模板、目录和角色覆盖值，然后把最终数值复制进本局 `ActorSheet`。运行中的 GM 不能提交目录版本、基础值、公式或任意新键；未知键返回稳定的 `unknown_ability` 错误。
