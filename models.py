from __future__ import annotations

import dataclasses
import random
import time
import typing
from itertools import chain

from django.db import models
from django.utils.functional import cached_property
from eventbase.base_models import BaseConfig, BaseModel, HistoryMixin
from freefire.config import config
from lib import error
from lib.decorators import cached_get

from event import constants
from event.data_schema import DrawLogSchema, RedeemLogSchema
from event.pisco import BasePlayer, BaseReward, BaseSetting, EventExchangeMixin


class Setting(BaseSetting):

    event_type = models.SmallIntegerField(
        default=BaseSetting.EventType.UNKNOWN,
        help_text="活动类型",
        choices=BaseSetting.EventType.choices,
    )
    has_redeem_page = models.PositiveSmallIntegerField(
        default=BaseModel.YesOrNo.N,
        choices=BaseModel.YesOrNo.choices,
        help_text="is reward redeem open",
    )
    rule = models.TextField(
        help_text="BR region Attention: Brazil's new regulations require all lottery events to list prize odds in rules, ensuring players understand odds"
    )
    draw_cost_one = models.IntegerField(help_text="gems cost of one draws")
    draw_cost_five = models.IntegerField(help_text="gems cost of five draws")

    @classmethod
    def all_reward_types(cls) -> list:
        return [Reward]


@dataclasses.dataclass
class PlayerCache(BasePlayer):

    token_num: int = 0
    redeemed_item_list: typing.List[int] = dataclasses.field(default_factory=list)
    reward_item_list: typing.List[int] = dataclasses.field(default_factory=list)
    # 当前免费时间段，抽奖时间列表
    free_draw_times: typing.List[int] = dataclasses.field(default_factory=list)

    @cached_property
    def setting(self) -> Setting:
        setting = Setting.get(self.region_lang)  # type:ignore
        if not setting:
            raise error.ValidateFailed(
                f"No {self.region_lang} setting found"  # type:ignore
            )
        return setting

    @cached_property
    def rewards(self) -> typing.Dict[int, Reward]:
        return {
            i.id: i for i in Reward.list(self.region_lang, self.group, 1)  # type:ignore
        }

    @cached_property
    def playerpool(self) -> typing.List[Reward]:
        """玩家奖池"""

        return [i for i in self.rewards.values() if i.id in self.reward_item_list]

    @cached_property
    def redeem_rewards(self) -> typing.Dict[int, RedeemReward]:
        return {
            i.id: i for i in RedeemReward.list(self.region_lang, "", 1)  # type:ignore
        }

    @cached_property
    def free_period(self) -> typing.Dict[int, FreePeriod]:
        return {i.id: i for i in FreePeriod.list(self.region_lang, 1)}  # type:ignore

    @cached_property
    def synced_owned_unique_items(self) -> typing.Set[int]:
        rewards = self.rewards.values()
        redeem_rewards = self.redeem_rewards.values()
        self.sync_owned_unique_items(chain(rewards, redeem_rewards))

        return self.owned_unique_items

    @property
    def prizepool(self) -> list:
        return [
            {
                "num": reward.item_num,
                "img": reward.item_img,
                "name": reward.item_name,
                "quality": reward.quality,
            }
            for reward in self.playerpool
        ]

    def get_redeem_rewards(
        self,
    ) -> typing.List[typing.Dict[str, typing.Union[str, int, bool]]]:
        redeem_pool = list()

        for i in self.redeem_rewards.values():
            redeem_pool.append(
                {
                    "id": i.id,
                    "name": i.item_name,
                    "img": i.item_img,
                    "num": i.item_num,
                    "quality": i.quality,
                    "cost": i.cost,
                    "redeemed": i.id in self.redeemed_item_list,
                    "owned": (
                        i.is_unique
                        and i.item_id in self.synced_owned_unique_items
                        or (i.item_id == constants.EP_CARD_ITEM_ID and self.has_ep)
                    ),
                }
            )
        return redeem_pool

    def history(self) -> list:
        history_list = list()
        spin_dict: typing.Dict[str, typing.Dict[str, typing.Union[int, list]]] = {}

        for i in Spin.list(self.character_id, self.group):  # type:ignore
            reward = self.rewards[i.item_pk]
            if i.trans_id in spin_dict:
                spin_dict[i.trans_id]["rewards"].append(  # type:ignore
                    {
                        "name": reward.item_name,
                        "img": reward.item_img,
                        "num": reward.item_num,
                        "quality": reward.quality,
                    }
                )
            else:
                spin_dict[i.trans_id] = {
                    "time": i.created_at,
                    "gems": i.gems,
                    "token": 0,
                    "rewards": [
                        {
                            "name": reward.item_name,
                            "img": reward.item_img,
                            "num": reward.item_num,
                            "quality": reward.quality,
                        }
                    ],
                }

        for spin_info in spin_dict.values():
            history_list.append(spin_info)

        for i in RedeemLog.list(self.character_id, self.group):  # type:ignore
            reward = self.redeem_rewards[i.item_pk]
            history_list.append(
                {
                    "time": i.created_at,
                    "gems": 0,
                    "token": i.cost,
                    "rewards": [
                        {
                            "name": reward.item_name,
                            "img": reward.item_img,
                            "num": reward.item_num,
                            "quality": reward.quality,
                        }
                    ],
                }
            )
        if history_list:
            history_list.sort(key=lambda x: x["time"], reverse=True)
        return history_list

    def get_free_period(
        self, created_at: int
    ) -> typing.Optional[typing.Type[FreePeriod]]:
        """判断当前时间是否处于免费抽奖时间段"""

        for period in self.free_period.values():
            if period.start_time <= created_at <= period.end_time:
                return period
        return None

    def get_left_free_nums(self, period: typing.Type[FreePeriod]) -> int:
        """获取当前时间段的剩余免费抽奖次数"""

        draw_num = 0
        for draw_time in self.free_draw_times:
            if period.start_time <= draw_time <= period.end_time:  # type:ignore
                draw_num += 1

        left_free_nums = period.free_num - draw_num  # type:ignore
        return left_free_nums if left_free_nums >= 0 else 0

    def build(self) -> None:
        super().build()
        self.token_num = 0
        self.redeemed_item_list.clear()
        self.reward_item_list.clear()
        self.free_draw_times.clear()

        player_db = PlayerDB.get(self.character_id)
        if not player_db:
            self.sync_group()
            self.reward_item_list = list(
                reward.id
                for reward in self.rewards.values()
                if reward.item_id != constants.EP_CARD_ITEM_ID or not self.has_ep
            )
            PlayerDB.objects.create(
                character_id=self.character_id,
                region_lang=self.region_lang,  # type:ignore
                group=self.group,  # type:ignore
                reward_item_list=self.reward_item_list,
            )
        else:
            self.region_lang = player_db.region_lang
            self.group = player_db.group
            self.reward_item_list = player_db.reward_item_list  # type:ignore

        # 初始化token数和免费抽奖次数
        for i in Spin.list(self.character_id, self.group):  # type:ignore
            if i.is_free:
                self.free_draw_times.append(i.created_at)
            if i.item_id == constants.TOKEN_ID:
                self.token_num += i.send_item_num

        # 初始化token数和兑换奖品列表
        for i in RedeemLog.list(self.character_id, self.group):  # type:ignore
            self.redeemed_item_list.append(i.item_pk)
            self.token_num -= i.cost

        self.should_save = True


class PlayerDB(BaseModel, HistoryMixin):
    class Meta:
        indexes = HistoryMixin.Meta.indexes

    DEFAULT_GROUP = "default"
    group = models.CharField(
        max_length=100,
        default=DEFAULT_GROUP,
        choices=((g, g) for g in config.CONTROL_GROUP),
    )
    reward_item_list = models.JSONField(
        default=list, help_text="player reward pool reward item list"
    )

    @classmethod
    def get(cls, character_id: str) -> typing.Type[PlayerDB]:
        return cls.objects.filter(character_id=character_id).first()


class Reward(BaseReward):
    priority = models.IntegerField(default=0, help_text="reward priority")
    is_grand = models.PositiveSmallIntegerField(
        default=BaseModel.YesOrNo.N,
        help_text="is grand reward",
        choices=BaseModel.YesOrNo.choices,
    )
    weight = models.IntegerField(default=0, help_text="weight")

    @classmethod
    @cached_get("event", 10, use="LOCAL_CACHE")
    @cached_get("event", 600)
    def list(
        cls: typing.Type["Reward"],
        region_lang: str,
        group: str,
        status: int,
        **_: typing.Any,
    ) -> typing.List["Reward"]:
        conditions: typing.Dict[str, typing.Any] = {"status": status, "group": group}
        if isinstance(cls.region_lang, models.query_utils.DeferredAttribute):
            conditions["region_lang"] = region_lang

        reward_list: typing.List[Reward] = list()
        reward_objs = cls.objects.filter(**conditions)
        grand_reward = next(
            (reward for reward in reward_objs if reward.is_grand == 1), None
        )
        token_reward = next(
            (reward for reward in reward_objs if reward.item_id == constants.TOKEN_ID),
            None,
        )
        if grand_reward:
            reward_list.append(grand_reward)
        if token_reward:
            reward_list.append(token_reward)
        reward_list.extend(
            sorted(
                [reward for reward in reward_objs if reward not in reward_list],
                key=lambda x: -x.priority,
            )
        )

        return reward_list


class RedeemReward(BaseReward):
    group = None
    cost = models.IntegerField(
        help_text="number of shard requied for reward redemption"
    )


class RedeemLog(BaseModel, HistoryMixin, EventExchangeMixin):
    class Meta:
        indexes = HistoryMixin.Meta.indexes

    DEFAULT_GROUP = "default"
    item_pk = models.IntegerField(default=-1)
    item_id = models.IntegerField(default=-1)
    item_num = models.IntegerField(default=0)
    item_name = models.CharField(default="", max_length=255)
    item_img = models.CharField(default="", max_length=255)
    trans_id = models.CharField(default="", max_length=255)
    send_item_id = models.IntegerField(default=-1)
    send_item_num = models.IntegerField(default=0)
    expire_time = models.IntegerField(default=-1)
    cost = models.IntegerField(help_text="cost of redeeming reward")
    group = models.CharField(
        max_length=100,
        default=DEFAULT_GROUP,
        choices=((g, g) for g in config.CONTROL_GROUP),
    )

    @classmethod
    def list(cls, character_id: str, group: str) -> typing.List[RedeemLog]:
        return list(cls.objects.filter(character_id=character_id, group=group))

    @classmethod
    def redeem(cls, player: PlayerCache, reward_id: int) -> None:
        if reward_id not in player.redeem_rewards:
            raise error.ParameterWrong("Invalid Request")
        if reward_id in player.redeemed_item_list:
            raise error.ParameterWrong("Invalid Request")
        redeem_reward = player.redeem_rewards[reward_id]
        if player.token_num < redeem_reward.cost:
            raise error.ParameterWrong("Invalid Request")

        stub = cls.exchange(player, [redeem_reward], 0)
        cls.objects.create(
            region_lang=player.region_lang,
            character_id=player.character_id,
            item_pk=redeem_reward.id,
            item_id=redeem_reward.item_id,
            item_name=redeem_reward.item_name,
            item_img=redeem_reward.item_img,
            item_num=redeem_reward.item_num,
            trans_id=stub.trans_id,
            send_item_id=constants.FF_TOKEN_ID
            if redeem_reward.is_token
            else redeem_reward.item_id,
            send_item_num=redeem_reward.token_num
            if redeem_reward.is_token
            else redeem_reward.item_num,
            cost=redeem_reward.cost,
            group=player.group,
        )
        player.token_num -= redeem_reward.cost
        player.redeemed_item_list.append(reward_id)
        player.save()
        RedeemLogSchema(
            region=player.region,
            account_id=player.character_id,
            account_group=player.group,
            reward_id=redeem_reward.id,
            item_id=redeem_reward.item_id,
            item_num=redeem_reward.item_num,
            trans_id=stub.trans_id,
        ).record()


class FreePeriod(BaseConfig):
    start_time = models.IntegerField(help_text="free spin start time")
    end_time = models.IntegerField(help_text="free spin end time")
    free_num = models.IntegerField(help_text="free spin number")

    @classmethod
    @cached_get("event", 10, use="LOCAL_CACHE")
    @cached_get("event", 600)
    def list(
        cls: typing.Type[FreePeriod],
        region_lang: str,
        status: int,
        **_: typing.Any,
    ) -> typing.List[FreePeriod]:
        conditions: typing.Dict[str, typing.Any] = {"status": status}
        if isinstance(cls.region_lang, models.query_utils.DeferredAttribute):
            conditions["region_lang"] = region_lang
        return list(cls.objects.filter(**conditions))


class Spin(BaseModel, HistoryMixin, EventExchangeMixin):
    class Meta:
        indexes = HistoryMixin.Meta.indexes

    DEFAULT_GROUP = "default"
    item_pk = models.IntegerField(default=-1)
    item_id = models.IntegerField(default=-1)
    item_num = models.IntegerField(default=0)
    item_name = models.CharField(default="", max_length=255)
    item_img = models.CharField(default="", max_length=255)
    trans_id = models.CharField(default="", max_length=255)
    send_item_id = models.IntegerField(default=-1)
    send_item_num = models.IntegerField(default=0)
    expire_time = models.IntegerField(default=-1)
    gems = models.IntegerField(help_text="耗钻数量")
    is_free = models.PositiveSmallIntegerField(default=0, help_text="is free")
    is_five = models.PositiveSmallIntegerField(default=0, help_text="is five")
    group = models.CharField(
        max_length=100,
        default=DEFAULT_GROUP,
        choices=((g, g) for g in config.CONTROL_GROUP),
    )

    @classmethod
    def list(cls, character_id: str, group: str) -> typing.List[Spin]:
        return list(cls.objects.filter(character_id=character_id, group=group))

    @classmethod
    def draw_rewards(cls, player: PlayerCache, draw: int) -> typing.List[Reward]:
        """玩家抽奖结果"""

        rewards = player.playerpool
        weights = [reward.weight for reward in rewards]
        return random.choices(rewards, weights=weights, k=draw)

    @classmethod
    def get_display_rewards(
        cls, player: PlayerCache, rewards: typing.List[Reward]
    ) -> typing.List[typing.Dict[str, typing.Union[str, int, bool]]]:
        display_rewards = list()
        for reward in rewards:
            display_rewards.append(
                {
                    "name": reward.item_name,
                    "img": reward.item_img,
                    "num": reward.item_num,
                    "quality": reward.quality,
                    "owned": (
                        reward.is_unique
                        and reward.item_id in player.synced_owned_unique_items
                        or (
                            reward.item_id == constants.EP_CARD_ITEM_ID
                            and player.has_ep
                        )
                    ),
                    "token": reward.item_num
                    if reward.item_id == constants.TOKEN_ID
                    else 0,
                }
            )
        return display_rewards

    @classmethod
    def draw(
        cls, player: PlayerCache, draw_num: int
    ) -> typing.List[typing.Dict[str, typing.Union[str, int, bool]]]:
        now = int(time.time())
        free_period = player.get_free_period(now)
        left_free_draw = player.get_left_free_nums(free_period) if free_period else 0
        draw_mapping = {
            constants.DRAW__FREE: 0,
            constants.DRAW__ONE: player.setting.draw_cost_one,
            constants.DRAW__FIVE: player.setting.draw_cost_five,
        }
        if draw_num not in draw_mapping:
            raise error.ParameterWrong("Invalid Request")

        if draw_num == constants.DRAW__FREE:
            # get free period
            if not free_period:
                raise error.ParameterWrong("ERROR_NO_FREE")

            if not left_free_draw:
                raise error.ParameterWrong("ERROR_NO_FREE")
        else:
            if player.synced_wallet.gems < draw_mapping[draw_num]:
                raise error.ParameterWrong("Invalid Request")

        gems = draw_mapping[draw_num]
        insert_lists = []
        draw_times = (
            draw_num if draw_num == constants.DRAW__FIVE else constants.DRAW__ONE
        )
        draw_rewards = cls.draw_rewards(player, draw_times)
        display_rewards = cls.get_display_rewards(player, draw_rewards)
        exchange_item = [
            reward for reward in draw_rewards if reward.item_id != constants.TOKEN_ID
        ]

        if exchange_item or gems:
            stub = cls.exchange(player, exchange_item, gems)
            trans_id = stub.trans_id

        else:
            stub = None
            trans_id = str(now)

        for reward in draw_rewards:
            insert_lists.append(
                {
                    "region_lang": player.region_lang,
                    "character_id": player.character_id,
                    "item_pk": reward.id,
                    "item_id": reward.item_id,
                    "item_name": reward.item_name,
                    "item_num": reward.item_num,
                    "item_img": reward.item_img,
                    "is_free": 1 if draw_num == constants.DRAW__FREE else 0,
                    "is_five": 1 if draw_num == constants.DRAW__FIVE else 0,
                    "send_item_id": constants.FF_TOKEN_ID
                    if reward.is_token
                    else reward.item_id,
                    "send_item_num": reward.token_num
                    if reward.is_token
                    else reward.item_num,
                    "gems": gems,
                    "group": player.group,
                    "created_at": now,
                    "trans_id": trans_id,
                }
            )
            if reward.item_id == constants.TOKEN_ID:
                player.token_num += reward.item_num

        cls.objects.bulk_create([cls(**insert_list) for insert_list in insert_lists])
        if draw_num == constants.DRAW__FREE:
            player.free_draw_times.append(now)
        player.save()
        if stub:
            cls.add_consume_log(player, stub, "draw", f"{draw_times} draw")
        DrawLogSchema(
            region=player.region,
            account_id=player.character_id,
            account_group=player.group,
            gems=gems,
            reason="draw",
            sub_reason=f"{draw_times} draw",
            trans_id=trans_id,
            grand_prize_type=player.setting.grand_prize_type,
            event_type=player.setting.event_type,
        ).record()

        return display_rewards
