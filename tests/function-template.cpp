template<typename T>
void FunctionTemplate();

template<typename T>
void FunctionTemplate() {}

class C
{
    template<typename T>
    void MethodTemplate();
};

template<typename T>
void C::MethodTemplate() {}
