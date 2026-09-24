function Data2 = tensor2angle(Ma)

% convert orientation tensor to angle in radians
if length(size(Ma)) == 3

    for st1 =  1:size(Ma,1)
        for st2 =  1:size(Ma,2)
              a = [Ma(st1,st2,1) Ma(st1,st2,3);Ma(st1,st2,2) Ma(st1,st2,4)];
            [V, D] = eig(a);
            Data2(st1,st2) =  atan(V(2,2)/V(1,2));
        end
    end

else

    for st =  1:size(Ma,1)
        for tt = 1:size(Ma,2)
            for zt = 1:size(Ma,3)
                a = [Ma(st,tt,zt, 1) Ma(st,tt,zt,3);Ma(st,tt,zt,2) Ma(st,tt,zt,4)];
                [V, D] = eig(a);
                Data2(st,tt,zt) =  atan(V(2,2)/V(1,2));
            end
        end
    end
    
end