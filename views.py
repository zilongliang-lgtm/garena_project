import time

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from eventbase.models import Transify
from eventbase.settings import config
from freefire import decorators
from lib.contrib.openapi import fields as F
from lib.contrib.openapi import router
from lib.http import response_success

from event import data_schema as schema
from event.models import PlayerCache, RedeemLog, Spin
from event.pisco import BaseCheckPolicy, CheckGoogleAnalytics, VisitSchema

GET = "GET"
POST = "POST"


@ensure_csrf_cookie
@decorators.ff_account_login
@PlayerCache.prepare
@router(GET, response_models=schema.INFO_RES)
def info(
    request: HttpRequest,
    access_token: str = F.Query(),
    region: str = F.Query(default=""),
    lang: str = F.Query(default=""),
) -> HttpResponse:
    player: PlayerCache = request.player  # type:ignore
    player.synced_owned_unique_items
    free_period = player.get_free_period(int(time.time()))
    res = {
        "uid": player.character_id,
        "gems": player.synced_wallet.gems,
        "token_num": player.token_num,
        "left_free_draw": player.get_left_free_nums(free_period) if free_period else 0,
        "free_until": free_period.end_time if free_period else 0,
        "draw_prices": [
            player.setting.draw_cost_one,
            player.setting.draw_cost_five,
        ],
        "pool": player.prizepool,
        "has_redeem_page": player.setting.has_redeem_page,
        "rule": player.setting.rule,
        "region": player.region,
        "lang": player.lang,
        "transify": Transify.get(player.region_lang),
        "data_log_name": config.PROJECT_NAME,
    }

    if player.setting.policy_enabled:
        res.update(
            {
                "policy_popup": {
                    "popup_title": player.setting.popup_title,
                    "popup_content": player.setting.popup_content,
                    "checked": player.policy_checked,
                }
            }
        )
    if player.setting.google_analytics_popup_enabled:
        res.update(
            {
                "google_analytics_popup": {
                    "ad_choice_text": player.setting.ad_choice_text,
                    "storage_choice_text": player.setting.storage_choice_text,
                    "popup_content": player.setting.google_analytics_popup_content,
                    "checked": player.google_analytics_checked,
                    "choices": player.google_analytics_choices,
                }
            }
        )
    player.should_save = True
    visit = VisitSchema(
        account_id=player.character_id, region=player.region, account_group=player.group
    )
    visit.record()

    return response_success(res)


@decorators.check_ff_login
@PlayerCache.prepare
@router(GET, response_models=schema.REDEEM_RES)
def get_redeem_rewards(request: HttpRequest) -> HttpResponse:
    player: PlayerCache = request.player  # type:ignore
    ret = {"rewards": player.get_redeem_rewards()}

    return response_success(ret)


@decorators.check_ff_login
@PlayerCache.prepare
@PlayerCache.locker
@router(POST, response_models=schema.DRAW_RES)
def draw(request: HttpRequest, data: schema.ViewDraw) -> HttpResponse:
    player: PlayerCache = request.player  # type:ignore
    draw_num = data.draw
    res = {"rewards": Spin.draw(player, draw_num)}
    return response_success(res)


@decorators.check_ff_login
@PlayerCache.prepare
@PlayerCache.locker
@router(POST, response_models=schema.COMMON_RES)
def redeem(request: HttpRequest, data: schema.ViewRedeem) -> HttpResponse:
    player: PlayerCache = request.player  # type:ignore
    reward_id = data.reward_id
    RedeemLog.redeem(player, reward_id)
    return response_success({})


@decorators.check_ff_login
@PlayerCache.prepare
@router(GET, response_models=schema.HISTORY_RES)
def history(request: HttpRequest) -> HttpResponse:
    player: PlayerCache = request.player  # type:ignore
    res = {"history": player.history()}
    return response_success(res)


@decorators.check_ff_login
@PlayerCache.prepare
@PlayerCache.locker
@router(POST, response_models=schema.COMMON_RES)
def policy_check(request: HttpRequest, ctx: schema.ViewPolicy) -> HttpResponse:
    player: PlayerCache = request.player  # type:ignore
    action = int(ctx.action)
    BaseCheckPolicy.check_policy(player=player, action=action)
    return response_success("")


@decorators.check_ff_login
@PlayerCache.prepare
@PlayerCache.locker
@router(POST, response_models=schema.COMMON_RES)
def google_analytics_check(request: HttpRequest, ctx: schema.ViewGA) -> HttpResponse:
    player: PlayerCache = request.player  # type:ignore
    CheckGoogleAnalytics.check_google_analytics(
        player=player,
        ad_storage=ctx.ad_storage,
        analytics_storage=ctx.analytics_storage,
        ad_user_data=ctx.ad_user_data,
        ad_personalization=ctx.ad_personalization,
    )
    return response_success("")
