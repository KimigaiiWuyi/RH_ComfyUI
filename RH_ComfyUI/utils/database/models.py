from typing import Optional

from sqlmodel import Field
from fastapi_amis_admin.amis.components import PageSchema

from gsuid_core.webconsole import site
from gsuid_core.webconsole.mount_app import GsAdminModel
from gsuid_core.utils.database.base_models import Bind

from ...rh_config.comfyui_config import RHCOMFYUI_CONFIG

DEFAULT_POINT: int = RHCOMFYUI_CONFIG.get_config("Default_Point").data


class RHBind(Bind, table=True):
    __table_args__ = {"extend_existing": True}
    point: int = Field(default=20, title="积分")

    @classmethod
    async def create_data(
        cls,
        user_id: str,
        bot_id: str,
        point: Optional[int] = None,
    ):
        if point is None:
            point = DEFAULT_POINT

        await cls.insert_data(
            group_id=None,
            user_id=user_id,
            bot_id=bot_id,
            point=point,
        )
        bind_data = await cls.select_data(
            user_id=user_id,
            bot_id=bot_id,
        )
        if bind_data is None:
            return cls(
                group_id=None,
                user_id=user_id,
                bot_id=bot_id,
                point=point,
            )
        return bind_data

    @classmethod
    async def add_point(
        cls,
        group_id: str,
        user_id: str,
        bot_id: str,
        add_point_num: int,
    ) -> int:
        bind_data = await cls.select_data(
            user_id=user_id,
            bot_id=bot_id,
        )
        if bind_data is None:
            bind_data = await cls.create_data(
                user_id=user_id,
                bot_id=bot_id,
            )

        bind_data.point += add_point_num
        await cls.update_data(
            user_id=user_id,
            bot_id=bot_id,
            point=bind_data.point,
        )
        return 0

    @classmethod
    async def get_point(
        cls,
        user_id: str,
        bot_id: str,
    ) -> int:
        bind_data = await cls.select_data(
            user_id=user_id,
            bot_id=bot_id,
        )
        if bind_data is None:
            return 0
        return bind_data.point

    @classmethod
    async def deduct_point(
        cls,
        user_id: str,
        bot_id: str,
        deduct_point_num: int,
    ) -> bool:
        bind_data = await cls.select_data(
            user_id=user_id,
            bot_id=bot_id,
        )
        if bind_data is None:
            bind_data = await cls.create_data(
                user_id=user_id,
                bot_id=bot_id,
            )

        if bind_data.point < deduct_point_num:
            return False

        bind_data.point -= deduct_point_num
        await cls.update_data(
            user_id=user_id,
            bot_id=bot_id,
            point=bind_data.point,
        )
        return True


@site.register_admin
class SsPushAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="AI绘图积分管理",
        icon="fa fa-bullhorn",
    )  # type: ignore

    # 配置管理模型
    model = RHBind
