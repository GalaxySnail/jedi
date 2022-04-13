from pep0484_generic_parameters import list_t_to_list_t

list_of_ints_and_strs: list[int | str]

# Test that unions are handled
x2 = list_t_to_list_t(list_of_ints_and_strs)[0]
#? int() str()
x2

for z in list_t_to_list_t(list_of_ints_and_strs):
    #? int() str()
    z


from pep0484_generic_passthroughs import (
    typed_variadic_tuple_generic_passthrough,
)

variadic_tuple_str_int: tuple[int | str, ...]

for m in typed_variadic_tuple_generic_passthrough(variadic_tuple_str_int):
    #? str() int()
    m


def func_returns_byteslike() -> bytes | bytearray:
    pass

#? bytes() bytearray()
func_returns_byteslike()


from typing import Union

def pep604_union(
    p: int | int,
    q: "int" | str | int,
    r: int | "bytes" | float,
    s: int | Union[str, "'float' | 'dict'"],
    t: Union[int, "str | Union[bytes, 'bytearray | list']"],
    u: int | None,
):
    #? int()
    p
    #? int() str()
    q
    #? int() bytes() float()
    r
    #? int() str() float() dict()
    s
    #? int() str() bytes() bytearray() list()
    t
    #? int() None
    u


from typing import AsyncIterator, TypeVar

T = TypeVar("T")

async def async_iterator(arg: T) -> AsyncIterator[bytes | bytearray | T]:
    pass

async def an_async_func():
    async for data in async_iterator(42):
        #? bytes() bytearray() int()
        data
